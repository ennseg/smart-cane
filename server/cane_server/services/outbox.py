import asyncio
import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import LinkPreviewOptions

from cane_server.bot.keyboards import sos_ack_keyboard
from cane_server.clock import Clock
from cane_server.config import Settings
from cane_server.storage import repositories
from cane_server.storage.database import Database
from cane_server.storage.models import LiveLocation, OutboxMessage, OutboxMethod, OutboxStatus

logger = logging.getLogger(__name__)

PERMANENT_TELEGRAM_ERRORS = (TelegramForbiddenError, TelegramBadRequest, TelegramNotFound)
BATCH_SIZE = 200
BASE_RETRY_DELAY_S = 2
MAX_RETRY_DELAY_S = 300
MAX_ERROR_LENGTH = 250


class OutboxDispatcher:
    def __init__(self, db: Database, bot: Bot, clock: Clock, settings: Settings) -> None:
        self._db = db
        self._bot = bot
        self._clock = clock
        self._settings = settings
        self._wakeup = asyncio.Event()

    def wake(self) -> None:
        self._wakeup.set()

    async def run_forever(self) -> None:
        while True:
            try:
                await self.deliver_due()
            except Exception:
                logger.exception("Ошибка цикла доставки уведомлений")
            await self._wait_for_work()

    async def _wait_for_work(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), timeout=self._settings.outbox_poll_s)
        self._wakeup.clear()

    async def deliver_due(self) -> int:
        now = self._clock.now()
        async with self._db.transaction() as session:
            messages = await repositories.pending_outbox_messages(session, BATCH_SIZE)
        blocked_chats: set[int] = set()
        delivered = 0
        for message in messages:
            if message.chat_id in blocked_chats:
                continue
            if message.next_attempt_at > now or not await self._deliver(message):
                blocked_chats.add(message.chat_id)
                continue
            delivered += 1
        return delivered

    async def _deliver(self, message: OutboxMessage) -> bool:
        try:
            telegram_message_id = await self._call_telegram(message)
        except TelegramRetryAfter as error:
            await self._schedule_retry(message, str(error), error.retry_after)
            return False
        except PERMANENT_TELEGRAM_ERRORS as error:
            logger.warning(
                "Сообщение %s в чат %s отброшено: %s", message.id, message.chat_id, error
            )
            await self._mark_failed(message, str(error))
            return True
        except Exception as error:
            logger.warning(
                "Сообщение %s в чат %s не доставлено: %s", message.id, message.chat_id, error
            )
            await self._schedule_retry(message, str(error), None)
            return False
        await self._mark_sent(message, telegram_message_id)
        return True

    async def _call_telegram(self, message: OutboxMessage) -> int | None:
        payload = message.payload
        match message.method:
            case OutboxMethod.TEXT:
                return await self._send_text(message.chat_id, payload)
            case OutboxMethod.LOCATION | OutboxMethod.LIVE_LOCATION:
                sent = await self._bot.send_location(
                    chat_id=message.chat_id,
                    latitude=payload["lat"],
                    longitude=payload["lon"],
                    live_period=payload.get("live_period"),
                )
                return sent.message_id
            case OutboxMethod.EDIT_LIVE:
                await self._bot.edit_message_live_location(
                    chat_id=message.chat_id,
                    message_id=payload["message_id"],
                    latitude=payload["lat"],
                    longitude=payload["lon"],
                )
                return None
        raise ValueError(f"Неизвестный метод outbox: {message.method}")

    async def _send_text(self, chat_id: int, payload: dict[str, object]) -> int:
        ack_event_id = payload.get("sos_ack_event_id")
        markup = sos_ack_keyboard(int(str(ack_event_id))) if ack_event_id is not None else None
        sent = await self._bot.send_message(
            chat_id=chat_id,
            text=str(payload["text"]),
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return sent.message_id

    async def _mark_sent(self, message: OutboxMessage, telegram_message_id: int | None) -> None:
        now = self._clock.now()
        async with self._db.transaction() as session:
            stored = await repositories.get_outbox_message(session, message.id)
            if stored is None:
                return
            stored.status = OutboxStatus.SENT
            stored.attempts += 1
            stored.sent_at = now
            stored.sent_message_id = telegram_message_id
            if self._is_live_location(stored) and telegram_message_id is not None:
                await session.merge(_live_location(stored, telegram_message_id, now))

    @staticmethod
    def _is_live_location(message: OutboxMessage) -> bool:
        return message.method == OutboxMethod.LIVE_LOCATION and message.event_id is not None

    async def _schedule_retry(
        self, message: OutboxMessage, error: str, retry_after_s: int | None
    ) -> None:
        now = self._clock.now()
        async with self._db.transaction() as session:
            stored = await repositories.get_outbox_message(session, message.id)
            if stored is None:
                return
            stored.attempts += 1
            stored.last_error = error[:MAX_ERROR_LENGTH]
            if stored.attempts >= self._settings.outbox_max_attempts:
                stored.status = OutboxStatus.FAILED
                return
            delay = retry_after_s if retry_after_s is not None else _backoff(stored.attempts)
            stored.next_attempt_at = now + delay

    async def _mark_failed(self, message: OutboxMessage, error: str) -> None:
        async with self._db.transaction() as session:
            stored = await repositories.get_outbox_message(session, message.id)
            if stored is None:
                return
            stored.attempts += 1
            stored.status = OutboxStatus.FAILED
            stored.last_error = error[:MAX_ERROR_LENGTH]


def _backoff(attempts: int) -> int:
    return min(BASE_RETRY_DELAY_S * 2 ** (attempts - 1), MAX_RETRY_DELAY_S)


def _live_location(message: OutboxMessage, telegram_message_id: int, now: int) -> LiveLocation:
    live_period = int(message.payload.get("live_period", 0))
    return LiveLocation(
        event_id=message.event_id,
        chat_id=message.chat_id,
        message_id=telegram_message_id,
        live_until=now + live_period,
    )
