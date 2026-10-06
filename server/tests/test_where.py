from cane_server.bot.dry_run import RecordingSession
from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.context import AppContext
from cane_server.storage import repositories
from cane_server.storage.models import CommandStatus
from tests.conftest import (
    GUARDIAN_A,
    GUARDIAN_B,
    DeviceClient,
    FakeClock,
    delivered,
    locations_to,
    texts_to,
)


async def command_status(ctx: AppContext) -> str | None:
    async with ctx.db.transaction() as session:
        command = await repositories.oldest_active_command(session, "cane-01", ctx.clock.now())
    return command.status if command else None


async def test_where_for_device_without_positions(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient
) -> None:
    replies = await chat.send_text(GUARDIAN_A, "/where")
    assert len(replies) == 1
    assert "позиция пока неизвестна" in replies[0].text
    assert "Запросил свежую позицию" in replies[0].text
    assert await command_status(ctx) == CommandStatus.PENDING


async def test_where_answers_last_position_with_age(
    chat: SimulatedChat, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    await bound_cane.emit(lat=59.95, lon=30.31, sats=7)
    clock.advance(5 * 60 + 10)
    replies = await chat.send_text(GUARDIAN_A, "/where")
    assert "<b>5 мин назад</b>" in replies[0].text
    assert "спутников: 7" in replies[0].text
    assert replies[1].method == "sendLocation"
    assert (replies[1].params["latitude"], replies[1].params["longitude"]) == (59.95, 30.31)


async def test_full_where_scenario_sends_fresh_position_to_requesters_only(
    ctx: AppContext,
    chat: SimulatedChat,
    bound_cane: DeviceClient,
    telegram: RecordingSession,
    clock: FakeClock,
) -> None:
    await bound_cane.emit(lat=59.95, lon=30.31)
    await chat.send_text(GUARDIAN_A, "/where")

    clock.advance(30)
    reply = await bound_cane.emit(lat=59.951, lon=30.311)
    assert reply["cmd"] == "locate_now"
    assert await command_status(ctx) == CommandStatus.DELIVERED
    assert await delivered(ctx, telegram) == []

    clock.advance(3)
    reply = await bound_cane.emit(lat=59.952, lon=30.312)
    assert reply["cmd"] is None
    calls = await delivered(ctx, telegram)
    assert "Свежая позиция: Трость Ивана" in texts_to(calls, GUARDIAN_A)[0]
    assert locations_to(calls, GUARDIAN_A)[0].params["latitude"] == 59.952
    assert texts_to(calls, GUARDIAN_B) == []
    assert await command_status(ctx) is None


async def test_two_requesters_share_one_command(
    ctx: AppContext,
    chat: SimulatedChat,
    bound_cane: DeviceClient,
    telegram: RecordingSession,
) -> None:
    await chat.send_text(GUARDIAN_A, "/where")
    await chat.send_text(GUARDIAN_B, "/where")
    repeated = await chat.send_text(GUARDIAN_A, "/where")
    assert "уже отправлен" in repeated[0].text

    await bound_cane.emit()
    await bound_cane.emit()
    calls = await delivered(ctx, telegram)
    assert len(texts_to(calls, GUARDIAN_A)) == 1
    assert len(texts_to(calls, GUARDIAN_B)) == 1


async def test_buffered_packet_does_not_answer_where(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await chat.send_text(GUARDIAN_A, "/where")
    await bound_cane.emit()
    await bound_cane.emit(buffered=True, ts=ctx.clock.now() - 600)
    assert texts_to(await delivered(ctx, telegram), GUARDIAN_A) == []
    assert await command_status(ctx) == CommandStatus.DELIVERED


async def test_answer_without_fix_is_reported(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await chat.send_text(GUARDIAN_A, "/where")
    await bound_cane.emit()
    await bound_cane.emit(fix=False, lat=None, lon=None, sats=1)
    calls = await delivered(ctx, telegram)
    assert "спутники сейчас не видны" in texts_to(calls, GUARDIAN_A)[0]
    assert locations_to(calls, GUARDIAN_A) == []


async def test_where_expires_when_device_does_not_answer(
    ctx: AppContext,
    chat: SimulatedChat,
    bound_cane: DeviceClient,
    telegram: RecordingSession,
    clock: FakeClock,
) -> None:
    await chat.send_text(GUARDIAN_A, "/where")
    clock.advance(ctx.settings.locate_timeout_s)
    report = await ctx.monitor.run_once()
    assert report.expired_commands == 1
    calls = await delivered(ctx, telegram)
    assert "не ответила на запрос позиции" in texts_to(calls, GUARDIAN_A)[0]
    reply = await bound_cane.emit()
    assert reply["cmd"] is None


async def test_where_mentions_silent_device(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    await bound_cane.emit()
    clock.advance(10 * 60)
    await ctx.monitor.run_once()
    replies = await chat.send_text(GUARDIAN_A, "/where")
    assert "не выходит на связь" in replies[0].text
