from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cane_server.storage import repositories

if TYPE_CHECKING:
    from cane_server.context import AppContext

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def chat_id_of(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        return event.from_user.id
    return None


class BoundGuardianMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        ctx: AppContext = data["ctx"]
        chat_id = chat_id_of(event)
        if chat_id is None:
            return None
        async with ctx.db.transaction() as session:
            devices = list(await repositories.devices_of_guardian(session, chat_id))
        if not devices:
            await self._reject(event, ctx)
            return None
        data["devices"] = devices
        return await handler(event, data)

    @staticmethod
    async def _reject(event: TelegramObject, ctx: "AppContext") -> None:
        text = ctx.texts.not_bound()
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer("Вы не привязаны к трости", show_alert=True)
