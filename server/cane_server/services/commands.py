from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.api.schemas import TelemetryPacket
from cane_server.services.notifier import MapPoint, Notification, Notifier
from cane_server.services.texts import Texts
from cane_server.storage import repositories
from cane_server.storage.models import (
    CommandStatus,
    CommandWaiter,
    Device,
    DeviceCommand,
    Position,
)

LOCATE_NOW = "locate_now"


@dataclass(frozen=True)
class LocateRequest:
    command_id: int
    already_waiting: bool


class CommandService:
    def __init__(self, notifier: Notifier, texts: Texts, locate_timeout_s: int) -> None:
        self._notifier = notifier
        self._texts = texts
        self._locate_timeout_s = locate_timeout_s

    async def request_location(
        self, session: AsyncSession, device_id: str, chat_id: int, now: int
    ) -> LocateRequest:
        command = await repositories.active_command(session, device_id, LOCATE_NOW, now)
        if command is None:
            command = self._new_locate_command(device_id, now)
            session.add(command)
            await session.flush()
        if await repositories.find_command_waiter(session, command.id, chat_id) is not None:
            return LocateRequest(command_id=command.id, already_waiting=True)
        session.add(CommandWaiter(command_id=command.id, chat_id=chat_id))
        return LocateRequest(command_id=command.id, already_waiting=False)

    def _new_locate_command(self, device_id: str, now: int) -> DeviceCommand:
        return DeviceCommand(
            device_id=device_id,
            name=LOCATE_NOW,
            status=CommandStatus.PENDING,
            created_at=now,
            expires_at=now + self._locate_timeout_s,
        )

    async def take_command_for_reply(
        self, session: AsyncSession, device_id: str, now: int
    ) -> str | None:
        command = await repositories.oldest_active_command(session, device_id, now)
        if command is None:
            return None
        if command.status == CommandStatus.PENDING:
            command.status = CommandStatus.DELIVERED
            command.delivered_at = now
        return command.name

    async def on_packet(
        self,
        session: AsyncSession,
        device: Device,
        packet: TelemetryPacket,
        position: Position | None,
        now: int,
    ) -> None:
        if packet.buffered:
            return
        for command in await repositories.delivered_commands(session, device.id, LOCATE_NOW):
            await self._complete_locate(session, device, command, position, now)

    async def _complete_locate(
        self,
        session: AsyncSession,
        device: Device,
        command: DeviceCommand,
        position: Position | None,
        now: int,
    ) -> None:
        command.status = CommandStatus.DONE
        command.completed_at = now
        waiters = await repositories.command_waiter_ids(session, command.id)
        self._notifier.to_chats(session, waiters, self._locate_result(device, position, now), now)

    def _locate_result(self, device: Device, position: Position | None, now: int) -> Notification:
        if position is None:
            return Notification(text=self._texts.locate_without_fix(device))
        return Notification(
            text=self._texts.fresh_position(device, position, now),
            point=MapPoint(position.lat, position.lon),
        )

    async def expire_overdue(self, session: AsyncSession, now: int) -> int:
        overdue = await repositories.overdue_commands(session, now)
        for command in overdue:
            command.status = CommandStatus.EXPIRED
            await self._notify_timeout(session, command, now)
        return len(overdue)

    async def _notify_timeout(
        self, session: AsyncSession, command: DeviceCommand, now: int
    ) -> None:
        if command.name != LOCATE_NOW:
            return
        device = await repositories.get_device(session, command.device_id)
        if device is None:
            return
        waiters = await repositories.command_waiter_ids(session, command.id)
        text = self._texts.locate_timeout(device, self._locate_timeout_s)
        self._notifier.to_chats(session, waiters, Notification(text=text), now)
