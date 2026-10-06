from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.storage.models import (
    BindCode,
    CommandStatus,
    CommandWaiter,
    Device,
    DeviceCommand,
    DeviceGuardian,
    Event,
    EventType,
    Guardian,
    LiveLocation,
    OutboxMessage,
    OutboxStatus,
    Packet,
    Position,
)

ACTIVE_COMMAND_STATUSES = (CommandStatus.PENDING, CommandStatus.DELIVERED)


async def get_device(session: AsyncSession, device_id: str) -> Device | None:
    return await session.get(Device, device_id)


async def find_device_by_token_hash(session: AsyncSession, token_hash: str) -> Device | None:
    query = select(Device).where(Device.token_hash == token_hash)
    return await session.scalar(query)


async def list_devices(session: AsyncSession) -> Sequence[Device]:
    return (await session.scalars(select(Device).order_by(Device.id))).all()


async def list_enabled_devices_seen_before(session: AsyncSession) -> Sequence[Device]:
    query = select(Device).where(Device.enabled.is_(True), Device.last_seen_at.is_not(None))
    return (await session.scalars(query)).all()


async def packet_exists(session: AsyncSession, device_id: str, seq: int) -> bool:
    query = select(Packet.id).where(Packet.device_id == device_id, Packet.seq == seq)
    return await session.scalar(query) is not None


async def latest_position(session: AsyncSession, device_id: str) -> Position | None:
    query = (
        select(Position)
        .where(Position.device_id == device_id)
        .order_by(Position.ts.desc(), Position.id.desc())
        .limit(1)
    )
    return await session.scalar(query)


async def recent_positions(session: AsyncSession, device_id: str, limit: int) -> Sequence[Position]:
    query = (
        select(Position)
        .where(Position.device_id == device_id)
        .order_by(Position.ts.desc(), Position.id.desc())
        .limit(limit)
    )
    return (await session.scalars(query)).all()


async def get_guardian(session: AsyncSession, chat_id: int) -> Guardian | None:
    return await session.get(Guardian, chat_id)


async def guardian_chat_ids(session: AsyncSession, device_id: str) -> list[int]:
    query = (
        select(DeviceGuardian.chat_id)
        .where(DeviceGuardian.device_id == device_id)
        .order_by(DeviceGuardian.bound_at, DeviceGuardian.chat_id)
    )
    return list((await session.scalars(query)).all())


async def devices_of_guardian(session: AsyncSession, chat_id: int) -> Sequence[Device]:
    query = (
        select(Device)
        .join(DeviceGuardian, DeviceGuardian.device_id == Device.id)
        .where(DeviceGuardian.chat_id == chat_id)
        .order_by(Device.id)
    )
    return (await session.scalars(query)).all()


async def find_binding(
    session: AsyncSession, device_id: str, chat_id: int
) -> DeviceGuardian | None:
    return await session.get(DeviceGuardian, (device_id, chat_id))


async def count_guardians(session: AsyncSession, device_id: str) -> int:
    query = select(func.count()).where(DeviceGuardian.device_id == device_id)
    return await session.scalar(query) or 0


async def find_bind_code(session: AsyncSession, code_hash: str) -> BindCode | None:
    return await session.get(BindCode, code_hash)


async def last_notified_event(
    session: AsyncSession, device_id: str, types: Sequence[EventType]
) -> Event | None:
    query = (
        select(Event)
        .where(Event.device_id == device_id, Event.type.in_(types), Event.notified.is_(True))
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(1)
    )
    return await session.scalar(query)


async def active_sos_events(session: AsyncSession, device_id: str, now: int) -> Sequence[Event]:
    query = select(Event).where(
        Event.device_id == device_id,
        Event.type == EventType.SOS,
        Event.live_until > now,
    )
    return (await session.scalars(query)).all()


async def unacked_sos_events(session: AsyncSession, device_id: str) -> Sequence[Event]:
    query = select(Event).where(
        Event.device_id == device_id,
        Event.type == EventType.SOS,
        Event.acked_at.is_(None),
        Event.next_reminder_at.is_not(None),
    )
    return (await session.scalars(query)).all()


async def due_sos_reminders(session: AsyncSession, now: int) -> Sequence[Event]:
    query = (
        select(Event)
        .where(
            Event.type == EventType.SOS,
            Event.acked_at.is_(None),
            Event.next_reminder_at <= now,
        )
        .order_by(Event.id)
    )
    return (await session.scalars(query)).all()


async def get_event(session: AsyncSession, event_id: int) -> Event | None:
    return await session.get(Event, event_id)


async def live_locations_of_event(session: AsyncSession, event_id: int) -> Sequence[LiveLocation]:
    query = select(LiveLocation).where(LiveLocation.event_id == event_id)
    return (await session.scalars(query)).all()


async def active_command(
    session: AsyncSession, device_id: str, name: str, now: int
) -> DeviceCommand | None:
    query = (
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.name == name,
            DeviceCommand.status.in_(ACTIVE_COMMAND_STATUSES),
            DeviceCommand.expires_at > now,
        )
        .order_by(DeviceCommand.id)
        .limit(1)
    )
    return await session.scalar(query)


async def oldest_active_command(
    session: AsyncSession, device_id: str, now: int
) -> DeviceCommand | None:
    query = (
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.status.in_(ACTIVE_COMMAND_STATUSES),
            DeviceCommand.expires_at > now,
        )
        .order_by(DeviceCommand.id)
        .limit(1)
    )
    return await session.scalar(query)


async def delivered_commands(
    session: AsyncSession, device_id: str, name: str
) -> Sequence[DeviceCommand]:
    query = select(DeviceCommand).where(
        DeviceCommand.device_id == device_id,
        DeviceCommand.name == name,
        DeviceCommand.status == CommandStatus.DELIVERED,
    )
    return (await session.scalars(query)).all()


async def overdue_commands(session: AsyncSession, now: int) -> Sequence[DeviceCommand]:
    query = select(DeviceCommand).where(
        DeviceCommand.status.in_(ACTIVE_COMMAND_STATUSES),
        DeviceCommand.expires_at <= now,
    )
    return (await session.scalars(query)).all()


async def command_waiter_ids(session: AsyncSession, command_id: int) -> list[int]:
    query = (
        select(CommandWaiter.chat_id)
        .where(CommandWaiter.command_id == command_id)
        .order_by(CommandWaiter.chat_id)
    )
    return list((await session.scalars(query)).all())


async def find_command_waiter(
    session: AsyncSession, command_id: int, chat_id: int
) -> CommandWaiter | None:
    return await session.get(CommandWaiter, (command_id, chat_id))


async def pending_outbox_messages(session: AsyncSession, limit: int) -> Sequence[OutboxMessage]:
    query = (
        select(OutboxMessage)
        .where(OutboxMessage.status == OutboxStatus.PENDING)
        .order_by(OutboxMessage.id)
        .limit(limit)
    )
    return (await session.scalars(query)).all()


async def get_outbox_message(session: AsyncSession, message_id: int) -> OutboxMessage | None:
    return await session.get(OutboxMessage, message_id)
