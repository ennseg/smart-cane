import itertools
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, cast

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

logger = logging.getLogger("telegram.dry_run")

BOT_USER = {
    "id": 123456789,
    "is_bot": True,
    "first_name": "Умная трость (DRY_RUN)",
    "username": "smart_cane_dry_run_bot",
}
METHODS_RETURNING_MESSAGE = frozenset(
    {
        "sendMessage",
        "sendLocation",
        "editMessageText",
        "editMessageLiveLocation",
        "stopMessageLiveLocation",
        "editMessageReplyMarkup",
    }
)
TEXT_PREVIEW_LIMIT = 400


@dataclass(frozen=True)
class SentCall:
    method: str
    chat_id: int | None
    params: dict[str, Any]

    @property
    def text(self) -> str:
        return str(self.params.get("text", ""))


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[SentCall] = []
        self.pending_failures: list[Exception] = []
        self._message_ids = itertools.count(1)

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Скачивание файлов недоступно в режиме DRY_RUN")

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        call = _to_call(method)
        if self.pending_failures:
            error = self.pending_failures.pop(0)
            logger.info("[TG ✗ %s] %s: %s", call.chat_id, call.method, error)
            raise error
        self.calls.append(call)
        logger.info("[TG → %s] %s", call.chat_id, describe_call(call))
        content = json.dumps({"ok": True, "result": self._fake_result(call)})
        response = self.check_response(bot=bot, method=method, status_code=200, content=content)
        return cast(TelegramType, response.result)

    def calls_to(self, chat_id: int) -> list[SentCall]:
        return [call for call in self.calls if call.chat_id == chat_id]

    def clear(self) -> None:
        self.calls.clear()

    def _fake_result(self, call: SentCall) -> Any:
        if call.method == "getMe":
            return BOT_USER
        if call.method in METHODS_RETURNING_MESSAGE:
            return self._fake_message(call)
        return True

    def _fake_message(self, call: SentCall) -> dict[str, Any]:
        message: dict[str, Any] = {
            "message_id": call.params.get("message_id", next(self._message_ids)),
            "date": int(time.time()),
            "chat": {"id": call.chat_id or 0, "type": "private"},
            "from": BOT_USER,
        }
        if "text" in call.params:
            message["text"] = call.params["text"]
        if "latitude" in call.params:
            message["location"] = {
                "latitude": call.params["latitude"],
                "longitude": call.params["longitude"],
            }
        return message


def _to_call(method: TelegramMethod[Any]) -> SentCall:
    dumped = method.model_dump(exclude_none=True)
    params = {key: value for key, value in dumped.items() if not isinstance(value, Default)}
    chat_id = params.get("chat_id")
    return SentCall(
        method=method.__api_method__,
        chat_id=int(chat_id) if isinstance(chat_id, int | str) else None,
        params=params,
    )


def describe_call(call: SentCall) -> str:
    params = call.params
    match call.method:
        case "sendMessage":
            return f"sendMessage{_buttons_suffix(params)}:\n{_shorten(call.text)}"
        case "sendLocation":
            live = f" live={params['live_period']}s" if "live_period" in params else ""
            return f"sendLocation {params['latitude']:.6f},{params['longitude']:.6f}{live}"
        case "editMessageLiveLocation":
            return (
                f"editMessageLiveLocation #{params['message_id']} "
                f"{params['latitude']:.6f},{params['longitude']:.6f}"
            )
        case "answerCallbackQuery":
            return f"answerCallbackQuery: {params.get('text', '')}"
    return call.method


def _buttons_suffix(params: dict[str, Any]) -> str:
    markup = params.get("reply_markup") or {}
    rows = markup.get("inline_keyboard") or []
    labels = [button["text"] for row in rows for button in row]
    return f" [кнопки: {', '.join(labels)}]" if labels else ""


def _shorten(text: str) -> str:
    if len(text) <= TEXT_PREVIEW_LIMIT:
        return text
    return text[:TEXT_PREVIEW_LIMIT] + "…"
