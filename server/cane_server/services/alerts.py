import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.api.schemas import PacketType, TelemetryPacket
from cane_server.config import Settings
from cane_server.services.notifier import MapPoint, Notification, Notifier
from cane_server.services.texts import Battery, Place, Texts
from cane_server.storage import repositories
from cane_server.storage.models import Device, Event, EventType, Position

logger = logging.getLogger(__name__)

SECONDS_PER_MINUTE = 60
ALWAYS_DELIVERED_WHEN_DELAYED = frozenset({EventType.SOS, EventType.BATT_CRIT})
GPS_EVENT_TYPES = (EventType.GPS_LOST, EventType.GPS_OK)


@dataclass(frozen=True)
class IncomingPacket:
    device: Device
    packet: TelemetryPacket
    packet_id: int
    ts: int
    position: Position | None


class SosAckOutcome(StrEnum):
    ACKED = "acked"
    ALREADY_ACKED = "already_acked"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class SosAckResult:
    outcome: SosAckOutcome
    acked_by_name: str | None = None


class AlertService:
    def __init__(self, settings: Settings, notifier: Notifier, texts: Texts) -> None:
        self._settings = settings
        self._notifier = notifier
        self._texts = texts
        self._cooldowns_s = {
            EventType.STILL: settings.cooldown_still_min * SECONDS_PER_MINUTE,
            EventType.BATT_LOW: settings.cooldown_batt_low_min * SECONDS_PER_MINUTE,
            EventType.BATT_CRIT: settings.cooldown_batt_crit_min * SECONDS_PER_MINUTE,
            EventType.GPS_LOST: settings.cooldown_gps_min * SECONDS_PER_MINUTE,
        }

    async def on_packet(self, session: AsyncSession, incoming: IncomingPacket, now: int) -> None:
        await self._update_sos_tracking(session, incoming, now)
        if incoming.packet.type == PacketType.POS:
            return
        place = await self._resolve_place(session, incoming)
        event = await self._record_event(session, incoming, place, now)
        reason = await self._suppression_reason(session, event, now)
        if reason is not None:
            event.suppressed_reason = reason
            logger.info("Событие %s от %s не отправлено: %s", event.type, event.device_id, reason)
            return
        event.notified = True
        notification = await self._build_notification(session, incoming, event, place, now)
        recipients = await self._notifier.to_guardians(
            session, incoming.device.id, notification, now
        )
        logger.info("Событие %s от %s → чаты %s", event.type, event.device_id, recipients)

    async def _update_sos_tracking(
        self, session: AsyncSession, incoming: IncomingPacket, now: int
    ) -> None:
        packet = incoming.packet
        if packet.buffered or not packet.has_valid_fix:
            return
        point = MapPoint(packet.lat or 0.0, packet.lon or 0.0)
        for event in await repositories.active_sos_events(session, incoming.device.id, now):
            for live in await repositories.live_locations_of_event(session, event.id):
                if live.live_until > now:
                    self._notifier.enqueue_live_edit(
                        session, live.chat_id, live.message_id, point, now
                    )

    async def _resolve_place(self, session: AsyncSession, incoming: IncomingPacket) -> Place:
        packet = incoming.packet
        if packet.has_valid_fix:
            return Place(MapPoint(packet.lat or 0.0, packet.lon or 0.0), incoming.ts, True)
        latest = await repositories.latest_position(session, incoming.device.id)
        if latest is not None:
            return Place(MapPoint(latest.lat, latest.lon), latest.ts, False)
        if packet.has_coordinates:
            return Place(MapPoint(packet.lat or 0.0, packet.lon or 0.0), None, False)
        return Place(None, None, False)

    async def _record_event(
        self, session: AsyncSession, incoming: IncomingPacket, place: Place, now: int
    ) -> Event:
        event = Event(
            device_id=incoming.device.id,
            packet_id=incoming.packet_id,
            type=EventType(incoming.packet.type.value),
            ts=incoming.ts,
            created_at=now,
            buffered=incoming.packet.buffered,
            lat=place.point.lat if place.point else None,
            lon=place.point.lon if place.point else None,
            notified=False,
            reminders_sent=0,
        )
        session.add(event)
        await session.flush()
        return event

    async def _suppression_reason(
        self, session: AsyncSession, event: Event, now: int
    ) -> str | None:
        if self._is_stale_delayed_event(event, now):
            return "stale"
        if event.type == EventType.GPS_OK and not await self._gps_loss_was_reported(
            session, event.device_id
        ):
            return "gps_loss_not_reported"
        if await self._is_in_cooldown(session, event, now):
            return "cooldown"
        return None

    def _is_stale_delayed_event(self, event: Event, now: int) -> bool:
        if not event.buffered or event.type in ALWAYS_DELIVERED_WHEN_DELAYED:
            return False
        max_age_s = self._settings.buffered_alert_max_age_min * SECONDS_PER_MINUTE
        return now - event.ts > max_age_s

    async def _gps_loss_was_reported(self, session: AsyncSession, device_id: str) -> bool:
        last = await repositories.last_notified_event(session, device_id, GPS_EVENT_TYPES)
        return last is not None and last.type == EventType.GPS_LOST

    async def _is_in_cooldown(self, session: AsyncSession, event: Event, now: int) -> bool:
        cooldown_s = self._cooldowns_s.get(EventType(event.type), 0)
        if cooldown_s <= 0:
            return False
        last = await repositories.last_notified_event(
            session, event.device_id, (EventType(event.type),)
        )
        return last is not None and now - last.created_at < cooldown_s

    async def _build_notification(
        self,
        session: AsyncSession,
        incoming: IncomingPacket,
        event: Event,
        place: Place,
        now: int,
    ) -> Notification:
        if event.type == EventType.SOS:
            return await self._sos_notification(session, incoming, event, place, now)
        text = self._event_text(incoming, place, now)
        if event.buffered:
            text += self._texts.delayed_note(event.ts, now)
        return Notification(text=text, point=place.point, event_id=event.id)

    def _event_text(self, incoming: IncomingPacket, place: Place, now: int) -> str:
        device = incoming.device
        battery = self._battery(incoming)
        match incoming.packet.type:
            case PacketType.STILL:
                return self._texts.still_alert(device, self._still_duration(incoming), battery)
            case PacketType.BATT_LOW:
                return self._texts.batt_low_alert(device, battery)
            case PacketType.BATT_CRIT:
                return self._texts.batt_crit_alert(device, battery)
            case PacketType.GPS_LOST:
                return self._texts.gps_lost_alert(device, place, battery, now)
            case PacketType.GPS_OK:
                return self._texts.gps_ok_alert(device)
        raise ValueError(f"Нет текста для события {incoming.packet.type}")

    def _still_duration(self, incoming: IncomingPacket) -> str:
        minutes = incoming.packet.still_min
        if minutes is None:
            return f"более {self._settings.still_default_min} мин"
        return f"{minutes} мин"

    @staticmethod
    def _battery(incoming: IncomingPacket) -> Battery:
        packet = incoming.packet
        device = incoming.device
        voltage = packet.bat_v if packet.bat_v is not None else device.bat_v
        percent = packet.bat_pct if packet.bat_pct is not None else device.bat_pct
        return Battery(voltage=voltage, percent=percent)

    async def _sos_notification(
        self,
        session: AsyncSession,
        incoming: IncomingPacket,
        event: Event,
        place: Place,
        now: int,
    ) -> Notification:
        await self._supersede_previous_sos(session, incoming.device.id, event.id, now)
        live_period_s = self._settings.sos_live_min * SECONDS_PER_MINUTE
        is_live = place.is_fix and not event.buffered
        event.live_until = now + live_period_s if is_live else None
        event.next_reminder_at = (
            now + self._settings.sos_remind_s if self._settings.sos_remind_max > 0 else None
        )
        text = self._texts.sos_alert(incoming.device, event.ts, self._battery(incoming), place, now)
        if event.buffered:
            text += self._texts.delayed_note(event.ts, now)
        return Notification(
            text=text,
            point=place.point,
            live_period_s=live_period_s if is_live else None,
            sos_ack_event_id=event.id,
            event_id=event.id,
        )

    async def _supersede_previous_sos(
        self, session: AsyncSession, device_id: str, current_event_id: int, now: int
    ) -> None:
        for previous in await repositories.unacked_sos_events(session, device_id):
            if previous.id != current_event_id:
                previous.next_reminder_at = None
        for previous in await repositories.active_sos_events(session, device_id, now):
            if previous.id != current_event_id:
                previous.live_until = now

    async def acknowledge_sos(
        self, session: AsyncSession, event_id: int, chat_id: int, now: int
    ) -> SosAckResult:
        event = await repositories.get_event(session, event_id)
        if event is None or event.type != EventType.SOS:
            return SosAckResult(SosAckOutcome.NOT_FOUND)
        if await repositories.find_binding(session, event.device_id, chat_id) is None:
            return SosAckResult(SosAckOutcome.NOT_FOUND)
        if event.acked_by_chat_id is not None:
            return SosAckResult(
                SosAckOutcome.ALREADY_ACKED,
                await self._guardian_name(session, event.acked_by_chat_id),
            )
        event.acked_by_chat_id = chat_id
        event.acked_at = now
        event.next_reminder_at = None
        name = await self._guardian_name(session, chat_id)
        await self._notify_other_guardians_about_ack(session, event, chat_id, name, now)
        return SosAckResult(SosAckOutcome.ACKED, name)

    async def _notify_other_guardians_about_ack(
        self, session: AsyncSession, event: Event, chat_id: int, name: str, now: int
    ) -> None:
        device = await repositories.get_device(session, event.device_id)
        if device is None:
            return
        others = [
            other
            for other in await repositories.guardian_chat_ids(session, device.id)
            if other != chat_id
        ]
        notification = Notification(text=self._texts.sos_acked_by(device, name))
        self._notifier.to_chats(session, others, notification, now)

    @staticmethod
    async def _guardian_name(session: AsyncSession, chat_id: int) -> str:
        guardian = await repositories.get_guardian(session, chat_id)
        return guardian.display_name if guardian else str(chat_id)

    async def send_due_sos_reminders(self, session: AsyncSession, now: int) -> int:
        due = await repositories.due_sos_reminders(session, now)
        for event in due:
            await self._send_sos_reminder(session, event, now)
        return len(due)

    async def _send_sos_reminder(self, session: AsyncSession, event: Event, now: int) -> None:
        event.reminders_sent += 1
        has_more = event.reminders_sent < self._settings.sos_remind_max
        event.next_reminder_at = now + self._settings.sos_remind_s if has_more else None
        device = await repositories.get_device(session, event.device_id)
        if device is None:
            return
        text = self._texts.sos_reminder(device, event.ts, event.reminders_sent, now)
        notification = Notification(text=text, sos_ack_event_id=event.id, event_id=event.id)
        await self._notifier.to_guardians(session, device.id, notification, now)
