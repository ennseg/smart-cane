from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.services.notifier import MapPoint, Notification, Notifier
from cane_server.services.texts import Texts
from cane_server.storage import repositories
from cane_server.storage.models import Device, Event, EventType, Position


class PresenceService:
    def __init__(
        self, notifier: Notifier, texts: Texts, silence_periods: int, started_at: int
    ) -> None:
        self._notifier = notifier
        self._texts = texts
        self._silence_periods = silence_periods
        self._started_at = started_at

    async def register_contact(self, session: AsyncSession, device: Device, now: int) -> None:
        if device.silence_alerted_at is not None and device.last_seen_at is not None:
            await self._announce_back_online(session, device, now - device.last_seen_at, now)
        device.silence_alerted_at = None
        device.last_seen_at = now

    async def _announce_back_online(
        self, session: AsyncSession, device: Device, offline_s: int, now: int
    ) -> None:
        event = _server_event(device.id, EventType.BACK_ONLINE, now, None)
        session.add(event)
        await session.flush()
        notification = Notification(
            text=self._texts.back_online(device, offline_s), event_id=event.id
        )
        await self._notifier.to_guardians(session, device.id, notification, now)

    async def check_silent_devices(self, session: AsyncSession, now: int) -> int:
        alerted = 0
        for device in await repositories.list_enabled_devices_seen_before(session):
            if self._is_newly_silent(device, now):
                await self._raise_silence_alert(session, device, now)
                alerted += 1
        return alerted

    def silence_threshold_s(self, device: Device) -> int:
        return self._silence_periods * device.period_s

    def _is_newly_silent(self, device: Device, now: int) -> bool:
        if device.silence_alerted_at is not None or device.last_seen_at is None:
            return False
        reference = max(device.last_seen_at, self._started_at)
        return now - reference > self.silence_threshold_s(device)

    async def _raise_silence_alert(self, session: AsyncSession, device: Device, now: int) -> None:
        device.silence_alerted_at = now
        position = await repositories.latest_position(session, device.id)
        event = _server_event(device.id, EventType.SILENT, now, position)
        session.add(event)
        await session.flush()
        silent_s = now - (device.last_seen_at or now)
        text = self._texts.silent_alert(device, silent_s, position.ts if position else None, now)
        point = MapPoint(position.lat, position.lon) if position else None
        notification = Notification(text=text, point=point, event_id=event.id)
        await self._notifier.to_guardians(session, device.id, notification, now)


def _server_event(
    device_id: str, event_type: EventType, now: int, position: Position | None
) -> Event:
    return Event(
        device_id=device_id,
        type=event_type,
        ts=now,
        created_at=now,
        buffered=False,
        lat=position.lat if position else None,
        lon=position.lon if position else None,
        notified=True,
        reminders_sent=0,
    )
