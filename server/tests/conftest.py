import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from cane_server.app import create_app
from cane_server.bot.dry_run import RecordingSession, SentCall
from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.config import Settings
from cane_server.context import AppContext, build_context
from cane_server.security import sign_body
from cane_server.services.devices import RegisteredDevice

START_TS = 1_791_018_000
GUARDIAN_A = 1001
GUARDIAN_B = 1002
STRANGER = 9999


class FakeClock:
    def __init__(self, start: int) -> None:
        self.current = start

    def now(self) -> int:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += seconds


class DeviceClient:
    def __init__(
        self, http: httpx.AsyncClient, registered: RegisteredDevice, clock: FakeClock
    ) -> None:
        self.http = http
        self.credentials = registered.credentials
        self.bind_code = registered.bind_code
        self.clock = clock
        self._next_seq = 1

    def packet(self, packet_type: str = "pos", **overrides: Any) -> dict[str, Any]:
        packet: dict[str, Any] = {
            "dev": self.credentials.device_id,
            "seq": self._take_seq(),
            "ts": self.clock.now(),
            "type": packet_type,
            "fix": True,
            "lat": 59.957155,
            "lon": 30.308288,
            "sats": 8,
            "hdop": 1.2,
            "bat_v": 3.85,
            "bat_pct": 52,
            "net": "wifi",
            "rssi": -61,
            "buffered": False,
        }
        packet.update(overrides)
        return packet

    def _take_seq(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq

    async def send(self, packet: dict[str, Any], path: str = "/api/v1/telemetry") -> httpx.Response:
        body = json.dumps(packet).encode("utf-8")
        return await self.send_raw(body, path=path)

    async def send_raw(
        self,
        body: bytes,
        path: str = "/api/v1/telemetry",
        token: str | None = None,
        signature: str | None = None,
        omit: tuple[str, ...] = (),
    ) -> httpx.Response:
        if signature is None:
            signature = sign_body(self.credentials.hmac_secret, body)
        headers = {
            "Content-Type": "application/json",
            "X-Device-Token": token if token is not None else self.credentials.token,
            "X-Signature": signature,
        }
        for header in omit:
            headers.pop(header)
        return await self.http.post(path, content=body, headers=headers)

    async def emit(self, packet_type: str = "pos", **overrides: Any) -> dict[str, Any]:
        response = await self.send(self.packet(packet_type, **overrides))
        assert response.status_code == 200, response.text
        return response.json()


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "dry_run": True,
        "bot_token": "",
        "database_url": f"sqlite+aiosqlite:///{tmp_path.as_posix()}/test.db",
        "display_tz": "Europe/Moscow",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START_TS)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def ctx(settings: Settings, clock: FakeClock) -> AsyncIterator[AppContext]:
    context = build_context(settings, clock=clock, bot_session=RecordingSession())
    await context.db.create_schema()
    yield context
    await context.db.dispose()


@pytest.fixture
def telegram(ctx: AppContext) -> RecordingSession:
    session = ctx.recording_session
    assert session is not None
    return session


@pytest.fixture
def chat(ctx: AppContext, telegram: RecordingSession) -> SimulatedChat:
    return SimulatedChat(ctx.dispatcher, ctx.bot, telegram)


@pytest.fixture
async def http(ctx: AppContext) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(context=ctx)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def register_device(
    ctx: AppContext, device_id: str = "cane-01", name: str = "Трость Ивана", period_s: int = 30
) -> RegisteredDevice:
    async with ctx.db.transaction() as session:
        return await ctx.devices.register(session, device_id, name, period_s, ctx.clock.now())


@pytest.fixture
async def cane(ctx: AppContext, http: httpx.AsyncClient, clock: FakeClock) -> DeviceClient:
    return DeviceClient(http, await register_device(ctx), clock)


async def issue_invite(ctx: AppContext, device_id: str, chat_id: int) -> str:
    async with ctx.db.transaction() as session:
        return ctx.binding.issue_invite_code(session, device_id, chat_id, ctx.clock.now()).code


@pytest.fixture
async def bound_cane(
    ctx: AppContext, cane: DeviceClient, chat: SimulatedChat, telegram: RecordingSession
) -> DeviceClient:
    await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}", first_name="Мария")
    second_code = await issue_invite(ctx, cane.credentials.device_id, GUARDIAN_A)
    await chat.send_text(GUARDIAN_B, f"/bind {second_code}", first_name="Пётр")
    await ctx.outbox.deliver_due()
    telegram.clear()
    return cane


async def delivered(ctx: AppContext, telegram: RecordingSession) -> list[SentCall]:
    start = len(telegram.calls)
    await ctx.outbox.deliver_due()
    return telegram.calls[start:]


def texts_to(calls: list[SentCall], chat_id: int) -> list[str]:
    return [call.text for call in calls if call.chat_id == chat_id and call.method == "sendMessage"]


def locations_to(calls: list[SentCall], chat_id: int) -> list[SentCall]:
    return [call for call in calls if call.chat_id == chat_id and call.method == "sendLocation"]
