import hashlib
import hmac
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from simulator.config import SequenceStore, SimulatorConfig
from simulator.physics import Walker, battery_percent

TELEMETRY_PATH = "/api/v1/telemetry"
OFFLINE_BUFFER_LIMIT = 200
REQUEST_TIMEOUT_S = 10


class Delivery(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRY_LATER = "retry_later"


@dataclass
class CaneState:
    walker: Walker = field(default_factory=Walker)
    fix: bool = True
    sats: int = 8
    hdop: float = 1.1
    bat_v: float = 4.05
    net: str = "wifi"
    rssi: int = -61
    online: bool = True


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def encode(packet: dict[str, Any]) -> bytes:
    return json.dumps(packet, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class VirtualCane:
    def __init__(
        self,
        config: SimulatorConfig,
        sequence: SequenceStore,
        client: httpx.Client,
        period_override_s: float | None,
        log: Callable[[str], None],
    ) -> None:
        self.config = config
        self.state = CaneState()
        self.buffer: list[dict[str, Any]] = []
        self._sequence = sequence
        self._client = client
        self._period_override_s = period_override_s
        self._server_period_s: float = 30
        self._locate_requested = False
        self._log = log

    @property
    def period_s(self) -> float:
        return self._period_override_s or self._server_period_s

    def build_packet(self, packet_type: str, **extra: Any) -> dict[str, Any]:
        state = self.state
        packet: dict[str, Any] = {
            "dev": self.config.dev,
            "seq": self._sequence.take(),
            "ts": int(time.time()),
            "type": packet_type,
            "fix": state.fix,
            "sats": state.sats,
            "hdop": state.hdop,
            "bat_v": round(state.bat_v, 2),
            "bat_pct": battery_percent(state.bat_v),
            "net": state.net,
            "rssi": state.rssi,
            "buffered": False,
        }
        packet.update(self._coordinates())
        packet.update(extra)
        return packet

    def _coordinates(self) -> dict[str, float]:
        walker = self.state.walker
        if not self.state.fix:
            return {"lat": round(walker.lat, 6), "lon": round(walker.lon, 6)}
        lat, lon = walker.jitter(3)
        return {"lat": round(lat, 6), "lon": round(lon, 6)}

    def emit(self, packet_type: str, **extra: Any) -> dict[str, Any]:
        packet = self.build_packet(packet_type, **extra)
        if not self.state.online:
            self._store_offline(packet)
            return packet
        self.flush_buffer()
        if self.deliver(packet) == Delivery.RETRY_LATER:
            self._store_offline(packet)
        self._serve_locate_requests()
        return packet

    def _store_offline(self, packet: dict[str, Any]) -> None:
        packet["buffered"] = True
        if len(self.buffer) >= OFFLINE_BUFFER_LIMIT:
            self.buffer.pop(0)
        self.buffer.append(packet)
        size = len(self.buffer)
        self._log(f"   [нет связи] seq={packet['seq']} {packet['type']} → OfflineBuffer ({size})")

    def flush_buffer(self, shuffle: bool = False) -> None:
        if not self.buffer:
            return
        pending = list(self.buffer)
        if shuffle:
            random.shuffle(pending)
        order = " (в случайном порядке)" if shuffle else ""
        self._log(f"   досылаю {len(pending)} накопленных записей{order}")
        self.buffer.clear()
        for packet in pending:
            if self.deliver(packet) == Delivery.RETRY_LATER:
                self.buffer.append(packet)

    def deliver(self, packet: dict[str, Any]) -> Delivery:
        body = encode(packet)
        try:
            response = self.post(body, self.auth_headers(body))
        except httpx.HTTPError as error:
            self._log(f"→ seq={packet['seq']} {packet['type']}: сеть недоступна ({error})")
            return Delivery.RETRY_LATER
        return self._handle_response(packet, response)

    def auth_headers(self, body: bytes) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Device-Token": self.config.token,
            "X-Signature": sign(self.config.secret, body),
        }

    def post(self, body: bytes, headers: dict[str, str]) -> httpx.Response:
        return self._client.post(
            self.config.url + TELEMETRY_PATH,
            content=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT_S,
        )

    def _handle_response(self, packet: dict[str, Any], response: httpx.Response) -> Delivery:
        summary = self._describe(packet)
        if response.status_code == 200:
            reply = response.json()
            self._apply_reply(reply)
            duplicate = " (дубликат)" if reply.get("dup") else ""
            self._log(f"→ {summary} ← 200 ack={reply['ack']}{duplicate} cmd={reply['cmd']}")
            return Delivery.ACCEPTED
        if 400 <= response.status_code < 500 and response.status_code != 429:
            self._log(f"→ {summary} ← {response.status_code} {response.text} (запись отброшена)")
            return Delivery.REJECTED
        self._log(f"→ {summary} ← {response.status_code}, повторю позже")
        return Delivery.RETRY_LATER

    def _describe(self, packet: dict[str, Any]) -> str:
        position = f"({packet['lat']:.5f}, {packet['lon']:.5f})" if "lat" in packet else "(—)"
        fix = "fix" if packet["fix"] else "нет фикса"
        buffered = " buffered" if packet["buffered"] else ""
        return (
            f"seq={packet['seq']} {packet['type']}{buffered} {position} {fix} "
            f"{packet['bat_pct']}% {packet['net']}"
        )

    def _apply_reply(self, reply: dict[str, Any]) -> None:
        period = reply.get("cfg", {}).get("period_s")
        if period:
            self._server_period_s = float(period)
        if reply.get("cmd") == "locate_now":
            self._locate_requested = True

    def _serve_locate_requests(self) -> None:
        while self._locate_requested:
            self._locate_requested = False
            self._log("   сервер просит locate_now → внеочередной фикс GPS")
            self.deliver(self.build_packet("pos"))
