from sqlalchemy import select

from cane_server.bot.dry_run import RecordingSession
from cane_server.bot.keyboards import SosAckCallback
from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.context import AppContext
from cane_server.storage.models import Event
from tests.conftest import (
    GUARDIAN_A,
    GUARDIAN_B,
    DeviceClient,
    FakeClock,
    delivered,
    locations_to,
    texts_to,
)


async def events(ctx: AppContext) -> list[Event]:
    async with ctx.db.transaction() as session:
        return list((await session.scalars(select(Event).order_by(Event.id))).all())


async def test_sos_goes_to_every_guardian_with_button_and_live_map(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("sos", lat=59.9601, lon=30.3101)
    calls = await delivered(ctx, telegram)

    for guardian in (GUARDIAN_A, GUARDIAN_B):
        text_calls = [c for c in calls if c.chat_id == guardian and c.method == "sendMessage"]
        assert len(text_calls) == 1
        assert "SOS! Трость Ивана просит помощи" in text_calls[0].text
        assert "52 %" in text_calls[0].text
        buttons = text_calls[0].params["reply_markup"]["inline_keyboard"][0]
        assert buttons[0]["text"] == "✅ Принял"
        location = locations_to(calls, guardian)
        assert len(location) == 1
        assert location[0].params["latitude"] == 59.9601
        assert location[0].params["live_period"] == 600


async def test_text_goes_before_map_for_each_chat(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("sos")
    calls = [c for c in await delivered(ctx, telegram) if c.chat_id == GUARDIAN_A]
    assert [c.method for c in calls] == ["sendMessage", "sendLocation"]


async def test_sos_without_fix_uses_last_known_position(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit(lat=59.95, lon=30.30)
    clock.advance(420)
    await bound_cane.emit("sos", fix=False, lat=None, lon=None)
    calls = await delivered(ctx, telegram)

    text = texts_to(calls, GUARDIAN_A)[0]
    assert "GPS-фикса нет" in text
    assert "7 мин назад" in text
    location = locations_to(calls, GUARDIAN_A)[0]
    assert (location.params["latitude"], location.params["longitude"]) == (59.95, 30.30)
    assert "live_period" not in location.params


async def test_sos_without_any_coordinates_sends_text_only(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("sos", fix=False, lat=None, lon=None)
    calls = await delivered(ctx, telegram)
    assert "координаты неизвестны" in texts_to(calls, GUARDIAN_A)[0]
    assert locations_to(calls, GUARDIAN_A) == []


async def test_positions_in_sos_mode_move_live_map(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("sos")
    await delivered(ctx, telegram)
    clock.advance(15)
    await bound_cane.emit(lat=59.9581, lon=30.3091)
    calls = await delivered(ctx, telegram)

    edits = [c for c in calls if c.method == "editMessageLiveLocation"]
    assert {c.chat_id for c in edits} == {GUARDIAN_A, GUARDIAN_B}
    assert all(c.params["latitude"] == 59.9581 for c in edits)


async def test_live_map_stops_after_sos_window(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("sos")
    await delivered(ctx, telegram)
    clock.advance(11 * 60)
    await bound_cane.emit()
    calls = await delivered(ctx, telegram)
    assert [c for c in calls if c.method == "editMessageLiveLocation"] == []


async def test_sos_ack_button_notifies_others_and_stops_reminders(
    ctx: AppContext,
    bound_cane: DeviceClient,
    telegram: RecordingSession,
    chat: SimulatedChat,
    clock: FakeClock,
) -> None:
    await bound_cane.emit("sos")
    await delivered(ctx, telegram)
    event_id = (await events(ctx))[0].id
    callback = SosAckCallback(event_id=event_id).pack()

    replies = await chat.press_button(GUARDIAN_A, callback, first_name="Мария")
    assert "Вы приняли тревогу" in replies[0].params["text"]
    calls = await delivered(ctx, telegram)
    assert texts_to(calls, GUARDIAN_B) == ["✅ Тревогу SOS от Трость Ивана принял(а) Мария."]
    assert texts_to(calls, GUARDIAN_A) == []

    again = await chat.press_button(GUARDIAN_B, callback, first_name="Пётр")
    assert "уже принял(а) Мария" in again[0].params["text"]

    clock.advance(600)
    report = await ctx.monitor.run_once()
    assert report.sos_reminders == 0


async def test_unacked_sos_is_reminded_limited_times(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("sos")
    await delivered(ctx, telegram)
    reminders: list[str] = []
    for _ in range(5):
        clock.advance(ctx.settings.sos_remind_s)
        await ctx.monitor.run_once()
        calls = await delivered(ctx, telegram)
        reminders += [t for t in texts_to(calls, GUARDIAN_A) if "Напоминание" in t]
    assert len(reminders) == ctx.settings.sos_remind_max
    assert "Напоминание #1" in reminders[0]


async def test_stranger_cannot_ack_sos(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, chat: SimulatedChat
) -> None:
    await bound_cane.emit("sos")
    event_id = (await events(ctx))[0].id
    replies = await chat.press_button(4242, SosAckCallback(event_id=event_id).pack())
    assert replies[0].method == "answerCallbackQuery"
    assert (await events(ctx))[0].acked_at is None


async def test_new_sos_supersedes_previous_reminders(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("sos")
    clock.advance(30)
    await bound_cane.emit("sos")
    first, second = await events(ctx)
    assert first.next_reminder_at is None
    assert second.next_reminder_at is not None
    calls = await delivered(ctx, telegram)
    assert len([t for t in texts_to(calls, GUARDIAN_A) if "SOS!" in t]) == 2


async def test_still_reports_minutes_and_map(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("still", still_min=12)
    calls = await delivered(ctx, telegram)
    assert "нет движения 12 мин" in texts_to(calls, GUARDIAN_A)[0]
    assert len(locations_to(calls, GUARDIAN_B)) == 1


async def test_still_without_minutes_uses_default_threshold(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("still")
    calls = await delivered(ctx, telegram)
    assert "нет движения более 10 мин" in texts_to(calls, GUARDIAN_A)[0]


async def test_still_antiflood_and_reminder_after_cooldown(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("still", still_min=10)
    clock.advance(60)
    await bound_cane.emit("still", still_min=11)
    calls = await delivered(ctx, telegram)
    assert len(texts_to(calls, GUARDIAN_A)) == 1
    assert (await events(ctx))[1].suppressed_reason == "cooldown"

    clock.advance(30 * 60)
    await bound_cane.emit("still", still_min=40)
    calls = await delivered(ctx, telegram)
    assert "нет движения 40 мин" in texts_to(calls, GUARDIAN_A)[0]


async def test_battery_low_is_sent_once_per_cooldown(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("batt_low", bat_v=3.58, bat_pct=14)
    clock.advance(600)
    await bound_cane.emit("batt_low", bat_v=3.57, bat_pct=13)
    calls = await delivered(ctx, telegram)
    texts = texts_to(calls, GUARDIAN_A)
    assert len(texts) == 1
    assert "низкий заряд" in texts[0]
    assert "14 % (3,58 В)" in texts[0]
    assert locations_to(calls, GUARDIAN_A)


async def test_battery_critical_is_sent_with_last_position(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("batt_low", bat_v=3.58, bat_pct=14)
    await bound_cane.emit("batt_crit", bat_v=3.44, bat_pct=5)
    calls = await delivered(ctx, telegram)
    texts = texts_to(calls, GUARDIAN_B)
    assert len(texts) == 2
    assert "критический разряд" in texts[1]
    assert len(locations_to(calls, GUARDIAN_B)) == 2


async def test_gps_lost_uses_last_fix_and_gps_ok_follows(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit(lat=59.95, lon=30.31)
    clock.advance(180)
    await bound_cane.emit("gps_lost", fix=False, lat=59.9, lon=30.2, sats=1)
    clock.advance(300)
    await bound_cane.emit("gps_ok", lat=59.96, lon=30.32)
    calls = await delivered(ctx, telegram)

    texts = texts_to(calls, GUARDIAN_A)
    assert "пропал сигнал GPS" in texts[0]
    assert "3 мин назад" in texts[0]
    assert "GPS восстановлен" in texts[1]
    lost_map, ok_map = locations_to(calls, GUARDIAN_A)
    assert (lost_map.params["latitude"], lost_map.params["longitude"]) == (59.95, 30.31)
    assert (ok_map.params["latitude"], ok_map.params["longitude"]) == (59.96, 30.32)


async def test_gps_lost_without_history_uses_device_coordinates(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("gps_lost", fix=False, lat=59.9, lon=30.2)
    calls = await delivered(ctx, telegram)
    assert "Последние координаты — на карте ниже" in texts_to(calls, GUARDIAN_A)[0]
    assert locations_to(calls, GUARDIAN_A)[0].params["latitude"] == 59.9


async def test_gps_ok_without_reported_loss_is_suppressed(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    await bound_cane.emit("gps_ok")
    assert texts_to(await delivered(ctx, telegram), GUARDIAN_A) == []
    assert (await events(ctx))[0].suppressed_reason == "gps_loss_not_reported"


async def test_gps_flapping_is_throttled_but_recovery_is_reported(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("gps_lost", fix=False, lat=None, lon=None)
    clock.advance(60)
    await bound_cane.emit("gps_lost", fix=False, lat=None, lon=None)
    clock.advance(60)
    await bound_cane.emit("gps_ok")
    clock.advance(60)
    await bound_cane.emit("gps_ok")
    texts = texts_to(await delivered(ctx, telegram), GUARDIAN_A)
    assert len(texts) == 2
    assert "пропал сигнал GPS" in texts[0]
    assert "GPS восстановлен" in texts[1]


async def test_delayed_sos_is_delivered_with_note(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    clock.advance(3600)
    await bound_cane.emit("sos", ts=clock.now() - 50 * 60, buffered=True)
    calls = await delivered(ctx, telegram)
    text = texts_to(calls, GUARDIAN_A)[0]
    assert "SOS!" in text
    assert "получено с задержкой" in text
    assert "50 мин назад" in text
    assert "live_period" not in locations_to(calls, GUARDIAN_A)[0].params


async def test_recent_buffered_event_is_delivered_with_note(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    clock.advance(3600)
    await bound_cane.emit("still", still_min=12, ts=clock.now() - 5 * 60, buffered=True)
    text = texts_to(await delivered(ctx, telegram), GUARDIAN_A)[0]
    assert "нет движения 12 мин" in text
    assert "получено с задержкой" in text


async def test_stale_buffered_event_is_stored_silently(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    clock.advance(7200)
    await bound_cane.emit("still", ts=clock.now() - 2 * 3600, buffered=True)
    assert texts_to(await delivered(ctx, telegram), GUARDIAN_A) == []
    stored = await events(ctx)
    assert stored[0].suppressed_reason == "stale"
    assert stored[0].notified is False


async def test_events_of_device_without_guardians_are_stored(
    ctx: AppContext, cane: DeviceClient, telegram: RecordingSession
) -> None:
    await cane.emit("sos")
    assert await delivered(ctx, telegram) == []
    assert (await events(ctx))[0].type == "sos"
