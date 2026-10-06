from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cane_server.storage.models import Device


class SosAckCallback(CallbackData, prefix="sos_ack"):
    event_id: int


class UnbindCallback(CallbackData, prefix="unbind"):
    device_id: str


def sos_ack_keyboard(event_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принял", callback_data=SosAckCallback(event_id=event_id))
    return builder.as_markup()


def unbind_keyboard(devices: Sequence[Device]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for device in devices:
        builder.button(
            text=f"Отвязаться от «{device.name}»",
            callback_data=UnbindCallback(device_id=device.id),
        )
    builder.adjust(1)
    return builder.as_markup()
