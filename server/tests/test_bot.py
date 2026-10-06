import pytest

from cane_server.bot.dry_run import RecordingSession
from cane_server.bot.keyboards import UnbindCallback
from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.context import AppContext
from cane_server.storage import repositories
from tests.conftest import (
    GUARDIAN_A,
    GUARDIAN_B,
    STRANGER,
    DeviceClient,
    FakeClock,
    delivered,
    register_device,
    texts_to,
)


async def test_start_for_new_user_explains_binding(chat: SimulatedChat) -> None:
    replies = await chat.send_text(STRANGER, "/start")
    assert len(replies) == 1
    assert "/bind ABCD-EFGH" in replies[0].text
    assert "/where" in replies[0].text


async def test_start_for_bound_user_has_no_binding_hint(
    chat: SimulatedChat, bound_cane: DeviceClient
) -> None:
    replies = await chat.send_text(GUARDIAN_A, "/start")
    assert "Чтобы начать" not in replies[0].text


async def test_help_lists_all_commands(chat: SimulatedChat) -> None:
    text = (await chat.send_text(STRANGER, "/help"))[0].text
    for command in ("/bind", "/where", "/status", "/history", "/invite", "/unbind", "/help"):
        assert command in text


@pytest.mark.parametrize("command", ["/where", "/status", "/history", "/invite", "/unbind"])
async def test_unbound_chat_is_refused(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient, command: str
) -> None:
    replies = await chat.send_text(STRANGER, command)
    assert len(replies) == 1
    assert "не привязаны" in replies[0].text
    async with ctx.db.transaction() as session:
        assert await repositories.active_command(session, "cane-01", "locate_now", 0) is None


async def test_unknown_text_gets_hint(chat: SimulatedChat) -> None:
    replies = await chat.send_text(STRANGER, "привет")
    assert "/help" in replies[0].text


async def test_status_before_first_contact(chat: SimulatedChat, bound_cane: DeviceClient) -> None:
    text = (await chat.send_text(GUARDIAN_A, "/status"))[0].text
    assert "ещё ни разу не выходила на связь" in text


async def test_status_shows_battery_network_satellites_and_contact(
    chat: SimulatedChat, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    await bound_cane.emit(net="gsm", rssi=-85, sats=9, hdop=0.9, bat_v=3.71, bat_pct=31)
    clock.advance(150)
    text = (await chat.send_text(GUARDIAN_B, "/status"))[0].text
    assert "31 % (3,71 В)" in text
    assert "GSM, -85 dBm" in text
    assert "9 спутников" in text
    assert "HDOP 0,9" in text
    assert "2 мин назад" in text
    assert "период передачи: 30 с" in text


async def test_history_returns_last_ten_points_newest_first(
    chat: SimulatedChat, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    for index in range(12):
        await bound_cane.emit(lat=59.9 + index / 1000)
        clock.advance(60)
    replies = await chat.send_text(GUARDIAN_A, "/history")
    text = replies[0].text
    assert "последние 10 точек" in text
    assert text.index("59.91100") < text.index("59.91000")
    assert "59.90100" not in text
    assert replies[1].method == "sendLocation"
    assert replies[1].params["latitude"] == pytest.approx(59.911)


async def test_history_without_points(chat: SimulatedChat, bound_cane: DeviceClient) -> None:
    replies = await chat.send_text(GUARDIAN_A, "/history")
    assert len(replies) == 1
    assert "точек маршрута пока нет" in replies[0].text


async def test_commands_answer_for_every_bound_device(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient
) -> None:
    second = await register_device(ctx, "cane-02", "Трость бабушки")
    await chat.send_text(GUARDIAN_A, f"/bind {second.bind_code}")
    replies = await chat.send_text(GUARDIAN_A, "/status")
    assert len(replies) == 2
    assert "Трость Ивана" in replies[0].text
    assert "Трость бабушки" in replies[1].text


async def test_unbind_flow(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    replies = await chat.send_text(GUARDIAN_B, "/unbind")
    keyboard = replies[0].params["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == UnbindCallback(device_id="cane-01").pack()

    replies = await chat.press_button(GUARDIAN_B, keyboard[0][0]["callback_data"])
    assert any("Вы отвязаны" in call.text for call in replies)

    await bound_cane.emit("sos")
    calls = await delivered(ctx, telegram)
    assert texts_to(calls, GUARDIAN_B) == []
    assert texts_to(calls, GUARDIAN_A)
    refused = await chat.send_text(GUARDIAN_B, "/where")
    assert "не привязаны" in refused[0].text


async def test_invite_issues_code_for_second_guardian(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient, telegram: RecordingSession
) -> None:
    await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}", first_name="Мария")
    invite = (await chat.send_text(GUARDIAN_A, "/invite"))[0].text
    code = invite.split("<code>")[1].split("</code>")[0]

    replies = await chat.send_text(GUARDIAN_B, f"/bind {code}", first_name="Пётр")
    assert "Сопровождающих у трости: 2" in replies[0].text
    calls = await delivered(ctx, telegram)
    assert any("подключился новый сопровождающий: Пётр" in t for t in texts_to(calls, GUARDIAN_A))
