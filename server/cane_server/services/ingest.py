from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.api.schemas import TelemetryPacket
from cane_server.services.alerts import AlertService, IncomingPacket
from cane_server.services.commands import CommandService
from cane_server.services.presence import PresenceService
from cane_server.storage import repositories
from cane_server.storage.models import Device, Packet, Position

MIN_TRUSTED_TS = 1_704_067_200
MAX_FUTURE_SKEW_S = 600


@dataclass(frozen=True)
class IngestResult:
    seq: int
    duplicate: bool
    command: str | None
    period_s: int


def resolve_effective_ts(device_ts: int | None, now: int) -> int:
    if device_ts is None or device_ts < MIN_TRUSTED_TS or device_ts > now + MAX_FUTURE_SKEW_S:
        return now
    return device_ts


class TelemetryIngestor:
    def __init__(
        self, presence: PresenceService, commands: CommandService, alerts: AlertService
    ) -> None:
        self._presence = presence
        self._commands = commands
        self._alerts = alerts

    async def handle(
        self, session: AsyncSession, device: Device, packet: TelemetryPacket, now: int
    ) -> IngestResult:
        await self.begin_exchange(session, device, now)
        duplicate = not await self.accept(session, device, packet, now)
        command = await self.finish_exchange(session, device, now)
        return IngestResult(
            seq=packet.seq, duplicate=duplicate, command=command, period_s=device.period_s
        )

    async def begin_exchange(self, session: AsyncSession, device: Device, now: int) -> None:
        await self._presence.register_contact(session, device, now)

    async def finish_exchange(self, session: AsyncSession, device: Device, now: int) -> str | None:
        return await self._commands.take_command_for_reply(session, device.id, now)

    async def accept(
        self, session: AsyncSession, device: Device, packet: TelemetryPacket, now: int
    ) -> bool:
        if await repositories.packet_exists(session, device.id, packet.seq):
            return False
        effective_ts = resolve_effective_ts(packet.ts, now)
        stored = await self._store_packet(session, device, packet, effective_ts, now)
        position = await self._store_position(session, device, packet, stored, effective_ts)
        _update_device_state(device, packet, effective_ts)
        await self._commands.on_packet(session, device, packet, position, now)
        incoming = IncomingPacket(device, packet, stored.id, effective_ts, position)
        await self._alerts.on_packet(session, incoming, now)
        return True

    @staticmethod
    async def _store_packet(
        session: AsyncSession,
        device: Device,
        packet: TelemetryPacket,
        effective_ts: int,
        now: int,
    ) -> Packet:
        stored = Packet(
            device_id=device.id,
            seq=packet.seq,
            type=packet.type.value,
            ts=packet.ts,
            effective_ts=effective_ts,
            received_at=now,
            buffered=packet.buffered,
            payload=packet.model_dump(mode="json", exclude_none=True),
        )
        session.add(stored)
        await session.flush()
        return stored

    @staticmethod
    async def _store_position(
        session: AsyncSession,
        device: Device,
        packet: TelemetryPacket,
        stored: Packet,
        effective_ts: int,
    ) -> Position | None:
        if not packet.has_valid_fix or packet.lat is None or packet.lon is None:
            return None
        position = Position(
            device_id=device.id,
            packet_id=stored.id,
            ts=effective_ts,
            lat=packet.lat,
            lon=packet.lon,
            sats=packet.sats,
            hdop=packet.hdop,
            net=packet.net.value if packet.net else None,
        )
        session.add(position)
        await session.flush()
        return position


def _update_device_state(device: Device, packet: TelemetryPacket, effective_ts: int) -> None:
    if not packet.buffered:
        device.last_packet_type = packet.type.value
    if device.state_ts is not None and effective_ts < device.state_ts:
        return
    device.state_ts = effective_ts
    device.fix = packet.fix
    device.sats = packet.sats if packet.sats is not None else device.sats
    device.hdop = packet.hdop if packet.hdop is not None else device.hdop
    device.bat_v = packet.bat_v if packet.bat_v is not None else device.bat_v
    device.bat_pct = packet.bat_pct if packet.bat_pct is not None else device.bat_pct
    device.net = packet.net.value if packet.net is not None else device.net
    device.rssi = packet.rssi if packet.rssi is not None else device.rssi
