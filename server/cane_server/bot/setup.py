from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from cane_server.bot.handlers import build_routers

BOT_COMMANDS = [
    BotCommand(command="where", description="Где сейчас трость"),
    BotCommand(command="status", description="Заряд, связь, спутники"),
    BotCommand(command="history", description="Последние точки маршрута"),
    BotCommand(command="bind", description="Привязаться по коду"),
    BotCommand(command="invite", description="Код для второго сопровождающего"),
    BotCommand(command="unbind", description="Отвязаться от трости"),
    BotCommand(command="help", description="Справка"),
]


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_routers(*build_routers())
    return dispatcher


async def register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
