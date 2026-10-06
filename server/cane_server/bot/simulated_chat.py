import itertools
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from cane_server.bot.dry_run import RecordingSession, SentCall


class SimulatedChat:
    def __init__(self, dispatcher: Dispatcher, bot: Bot, session: RecordingSession) -> None:
        self._dispatcher = dispatcher
        self._bot = bot
        self._session = session
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(100_000)

    async def send_text(
        self, chat_id: int, text: str, first_name: str = "Тестовый", username: str | None = None
    ) -> list[SentCall]:
        user = _user(chat_id, first_name, username)
        message = self._message(chat_id, user, text)
        return await self._feed(chat_id, {"message": message})

    async def press_button(
        self, chat_id: int, callback_data: str, first_name: str = "Тестовый"
    ) -> list[SentCall]:
        user = _user(chat_id, first_name, None)
        callback = {
            "id": f"cb-{next(self._update_ids)}",
            "from": user,
            "chat_instance": f"chat-{chat_id}",
            "data": callback_data,
            "message": self._message(chat_id, user, "кнопка"),
        }
        return await self._feed(chat_id, {"callback_query": callback})

    async def _feed(self, chat_id: int, payload: dict[str, Any]) -> list[SentCall]:
        start = len(self._session.calls)
        raw_update = {"update_id": next(self._update_ids), **payload}
        update = Update.model_validate(raw_update, context={"bot": self._bot})
        await self._dispatcher.feed_update(self._bot, update)
        return [call for call in self._session.calls[start:] if call.chat_id in (chat_id, None)]

    def _message(self, chat_id: int, user: dict[str, Any], text: str) -> dict[str, Any]:
        return {
            "message_id": next(self._message_ids),
            "date": 0,
            "chat": {"id": chat_id, "type": "private", "first_name": user["first_name"]},
            "from": user,
            "text": text,
        }


def _user(chat_id: int, first_name: str, username: str | None) -> dict[str, Any]:
    user: dict[str, Any] = {"id": chat_id, "is_bot": False, "first_name": first_name}
    if username:
        user["username"] = username
    return user
