from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from cane_server.api.errors import ApiError
from cane_server.bot.dry_run import SentCall, describe_call
from cane_server.bot.simulated_chat import SimulatedChat

router = APIRouter(prefix="/dev", tags=["dev"])


class DevChatRequest(BaseModel):
    chat_id: int
    text: str | None = None
    callback_data: str | None = None
    first_name: str = Field(default="Тестовый", min_length=1, max_length=64)
    username: str | None = None

    @model_validator(mode="after")
    def require_text_or_callback(self) -> "DevChatRequest":
        if (self.text is None) == (self.callback_data is None):
            raise ValueError("Укажите ровно одно из полей: text или callback_data")
        return self


class DevChatReply(BaseModel):
    replies: list[dict[str, Any]]


@router.post("/bot", response_model=DevChatReply)
async def talk_to_bot(request: Request, body: DevChatRequest) -> DevChatReply:
    chat: SimulatedChat | None = getattr(request.app.state, "simulated_chat", None)
    if chat is None:
        raise ApiError(404, "dev_disabled", "Отладочный чат доступен только в режиме DRY_RUN")
    if body.text is not None:
        calls = await chat.send_text(body.chat_id, body.text, body.first_name, body.username)
    else:
        calls = await chat.press_button(body.chat_id, body.callback_data or "", body.first_name)
    request.app.state.ctx.outbox.wake()
    return DevChatReply(replies=[_reply(call) for call in calls])


def _reply(call: SentCall) -> dict[str, Any]:
    return {"method": call.method, "summary": describe_call(call), "params": call.params}
