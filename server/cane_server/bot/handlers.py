from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message, User

from cane_server.bot.access import BoundGuardianMiddleware
from cane_server.bot.keyboards import SosAckCallback, UnbindCallback, unbind_keyboard
from cane_server.services.alerts import SosAckOutcome, SosAckResult
from cane_server.services.binding import BindOutcome, BindResult
from cane_server.services.texts import Texts
from cane_server.storage import repositories
from cane_server.storage.models import Device

if TYPE_CHECKING:
    from cane_server.context import AppContext


def display_name(user: User | None, chat_id: int) -> str:
    if user is None:
        return str(chat_id)
    if user.username:
        return f"{user.full_name} (@{user.username})"
    return user.full_name


async def handle_start(message: Message, ctx: "AppContext") -> None:
    async with ctx.db.transaction() as session:
        devices = await repositories.devices_of_guardian(session, message.chat.id)
    await message.answer(ctx.texts.start_text(is_bound=bool(devices)))


async def handle_help(message: Message, ctx: "AppContext") -> None:
    await message.answer(ctx.texts.help_text())


async def handle_bind(message: Message, command: CommandObject, ctx: "AppContext") -> None:
    if not command.args:
        await message.answer(ctx.texts.bind_usage())
        return
    now = ctx.clock.now()
    name = display_name(message.from_user, message.chat.id)
    async with ctx.db.transaction() as session:
        result = await ctx.binding.bind(session, message.chat.id, name, command.args, now)
    ctx.outbox.wake()
    await message.answer(bind_result_text(ctx.texts, result, now))


def bind_result_text(texts: Texts, result: BindResult, now: int) -> str:
    match result.outcome:
        case BindOutcome.BOUND if result.device is not None:
            return texts.bind_success(result.device, result.guardians_count)
        case BindOutcome.ALREADY_BOUND if result.device is not None:
            return texts.bind_already(result.device)
        case BindOutcome.USED_CODE:
            return texts.bind_used_code()
        case BindOutcome.EXPIRED_CODE:
            return texts.bind_expired_code()
        case BindOutcome.BLOCKED if result.blocked_until is not None:
            return texts.bind_blocked(result.blocked_until, now)
    return texts.bind_unknown_code()


async def handle_where(message: Message, ctx: "AppContext", devices: list[Device]) -> None:
    for device in devices:
        await answer_where(message, ctx, device.id)


async def answer_where(message: Message, ctx: "AppContext", device_id: str) -> None:
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        request = await ctx.commands.request_location(session, device_id, message.chat.id, now)
        device = await repositories.get_device(session, device_id)
        position = await repositories.latest_position(session, device_id)
    if device is None:
        return
    await message.answer(ctx.texts.where_reply(device, position, request.already_waiting, now))
    if position is not None:
        await message.answer_location(latitude=position.lat, longitude=position.lon)


async def handle_status(message: Message, ctx: "AppContext", devices: list[Device]) -> None:
    now = ctx.clock.now()
    for device in devices:
        await message.answer(ctx.texts.status_reply(device, now))


async def handle_history(message: Message, ctx: "AppContext", devices: list[Device]) -> None:
    now = ctx.clock.now()
    for device in devices:
        async with ctx.db.transaction() as session:
            positions = await repositories.recent_positions(
                session, device.id, ctx.settings.history_limit
            )
        await message.answer(ctx.texts.history_reply(device, positions, now))
        if positions:
            latest = positions[0]
            await message.answer_location(latitude=latest.lat, longitude=latest.lon)


async def handle_invite(message: Message, ctx: "AppContext", devices: list[Device]) -> None:
    now = ctx.clock.now()
    for device in devices:
        async with ctx.db.transaction() as session:
            issued = ctx.binding.issue_invite_code(session, device.id, message.chat.id, now)
        expires_at = issued.expires_at if issued.expires_at is not None else now
        await message.answer(ctx.texts.invite_code(device, issued.code, expires_at, now))


async def handle_unbind(message: Message, ctx: "AppContext", devices: list[Device]) -> None:
    await message.answer(ctx.texts.unbind_choose(), reply_markup=unbind_keyboard(devices))


async def handle_unbind_choice(
    callback: CallbackQuery, callback_data: UnbindCallback, ctx: "AppContext"
) -> None:
    chat_id = callback.from_user.id
    async with ctx.db.transaction() as session:
        device = await ctx.binding.unbind(session, callback_data.device_id, chat_id)
    await callback.answer()
    text = ctx.texts.unbind_done(device) if device else ctx.texts.unbind_not_bound()
    await ctx.bot.send_message(chat_id=chat_id, text=text)


async def handle_sos_ack(
    callback: CallbackQuery, callback_data: SosAckCallback, ctx: "AppContext"
) -> None:
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        result = await ctx.alerts.acknowledge_sos(
            session, callback_data.event_id, callback.from_user.id, now
        )
    ctx.outbox.wake()
    await callback.answer(sos_ack_answer(result), show_alert=True)


def sos_ack_answer(result: SosAckResult) -> str:
    match result.outcome:
        case SosAckOutcome.ACKED:
            return "Вы приняли тревогу. Остальные сопровождающие уведомлены."
        case SosAckOutcome.ALREADY_ACKED:
            return f"Тревогу уже принял(а) {result.acked_by_name}."
    return "Тревога не найдена."


async def handle_unknown(message: Message, ctx: "AppContext") -> None:
    await message.answer(ctx.texts.unknown_command())


def build_routers() -> list[Router]:
    public = Router(name="public")
    public.message.register(handle_start, CommandStart())
    public.message.register(handle_help, Command("help"))
    public.message.register(handle_bind, Command("bind"))

    guarded = Router(name="guarded")
    guarded.message.middleware(BoundGuardianMiddleware())
    guarded.callback_query.middleware(BoundGuardianMiddleware())
    guarded.message.register(handle_where, Command("where"))
    guarded.message.register(handle_status, Command("status"))
    guarded.message.register(handle_history, Command("history"))
    guarded.message.register(handle_invite, Command("invite"))
    guarded.message.register(handle_unbind, Command("unbind"))
    guarded.callback_query.register(handle_unbind_choice, UnbindCallback.filter())
    guarded.callback_query.register(handle_sos_ack, SosAckCallback.filter())

    fallback = Router(name="fallback")
    fallback.message.register(handle_unknown)
    return [public, guarded, fallback]
