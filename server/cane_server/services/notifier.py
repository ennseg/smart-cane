from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.storage import repositories
from cane_server.storage.models import OutboxMessage, OutboxMethod, OutboxStatus


@dataclass(frozen=True)
class MapPoint:
    lat: float
    lon: float


@dataclass(frozen=True)
class Notification:
    text: str
    point: MapPoint | None = None
    live_period_s: int | None = None
    sos_ack_event_id: int | None = None
    event_id: int | None = None


class Notifier:
    async def to_guardians(
        self, session: AsyncSession, device_id: str, notification: Notification, now: int
    ) -> list[int]:
        chat_ids = await repositories.guardian_chat_ids(session, device_id)
        self.to_chats(session, chat_ids, notification, now)
        return chat_ids

    def to_chats(
        self,
        session: AsyncSession,
        chat_ids: Iterable[int],
        notification: Notification,
        now: int,
    ) -> None:
        for chat_id in chat_ids:
            self.to_chat(session, chat_id, notification, now)

    def to_chat(
        self, session: AsyncSession, chat_id: int, notification: Notification, now: int
    ) -> None:
        text_payload: dict[str, object] = {"text": notification.text}
        if notification.sos_ack_event_id is not None:
            text_payload["sos_ack_event_id"] = notification.sos_ack_event_id
        self._enqueue(session, chat_id, OutboxMethod.TEXT, text_payload, notification, now)
        if notification.point is not None:
            self._enqueue_point(session, chat_id, notification, notification.point, now)

    def enqueue_live_edit(
        self, session: AsyncSession, chat_id: int, message_id: int, point: MapPoint, now: int
    ) -> None:
        payload = {"message_id": message_id, "lat": point.lat, "lon": point.lon}
        session.add(_outbox_row(chat_id, OutboxMethod.EDIT_LIVE, payload, None, now))

    def _enqueue_point(
        self,
        session: AsyncSession,
        chat_id: int,
        notification: Notification,
        point: MapPoint,
        now: int,
    ) -> None:
        payload: dict[str, object] = {"lat": point.lat, "lon": point.lon}
        method = OutboxMethod.LOCATION
        if notification.live_period_s is not None:
            payload["live_period"] = notification.live_period_s
            method = OutboxMethod.LIVE_LOCATION
        self._enqueue(session, chat_id, method, payload, notification, now)

    @staticmethod
    def _enqueue(
        session: AsyncSession,
        chat_id: int,
        method: OutboxMethod,
        payload: dict[str, object],
        notification: Notification,
        now: int,
    ) -> None:
        session.add(_outbox_row(chat_id, method, payload, notification.event_id, now))


def _outbox_row(
    chat_id: int,
    method: OutboxMethod,
    payload: dict[str, object],
    event_id: int | None,
    now: int,
) -> OutboxMessage:
    return OutboxMessage(
        chat_id=chat_id,
        method=method,
        payload=payload,
        event_id=event_id,
        status=OutboxStatus.PENDING,
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
