from pathlib import Path

from cane_server.bot.dry_run import RecordingSession
from cane_server.context import AppContext, build_context
from cane_server.storage import repositories
from tests.conftest import (
    GUARDIAN_A,
    GUARDIAN_B,
    DeviceClient,
    FakeClock,
    delivered,
    locations_to,
    make_settings,
    register_device,
    texts_to,
)

PERIOD_S = 30
THRESHOLD_S = 3 * PERIOD_S


async def test_silence_is_not_reported_before_threshold(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit()
    clock.advance(THRESHOLD_S)
    report = await ctx.monitor.run_once()
    assert report.silent_devices == 0
    assert await delivered(ctx, telegram) == []


async def test_silence_is_reported_once_with_last_position(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit(lat=59.95, lon=30.31)
    clock.advance(THRESHOLD_S + 1)
    assert (await ctx.monitor.run_once()).silent_devices == 1
    calls = await delivered(ctx, telegram)
    for guardian in (GUARDIAN_A, GUARDIAN_B):
        text = texts_to(calls, guardian)[0]
        assert "Трость Ивана не выходит на связь" in text
        assert "1 мин" in text
        assert locations_to(calls, guardian)[0].params["latitude"] == 59.95

    for _ in range(5):
        clock.advance(600)
        assert (await ctx.monitor.run_once()).silent_devices == 0
    assert await delivered(ctx, telegram) == []


async def test_contact_after_silence_is_announced(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit()
    clock.advance(THRESHOLD_S + 1)
    await ctx.monitor.run_once()
    await delivered(ctx, telegram)

    clock.advance(20 * 60)
    await bound_cane.emit()
    calls = await delivered(ctx, telegram)
    assert texts_to(calls, GUARDIAN_A) == [
        "✅ Связь с Трость Ивана восстановлена (не было 21 мин)."
    ]

    clock.advance(THRESHOLD_S + 1)
    assert (await ctx.monitor.run_once()).silent_devices == 1


async def test_silence_after_critical_battery_mentions_shutdown(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession, clock: FakeClock
) -> None:
    await bound_cane.emit("batt_crit", bat_v=3.44, bat_pct=5)
    await delivered(ctx, telegram)
    clock.advance(THRESHOLD_S + 1)
    await ctx.monitor.run_once()
    text = texts_to(await delivered(ctx, telegram), GUARDIAN_A)[0]
    assert "вероятно, трость выключилась" in text


async def test_device_that_never_connected_is_not_reported(
    ctx: AppContext, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    clock.advance(24 * 3600)
    assert (await ctx.monitor.run_once()).silent_devices == 0


async def test_disabled_device_is_not_reported(
    ctx: AppContext, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    await bound_cane.emit()
    async with ctx.db.transaction() as session:
        await ctx.devices.set_enabled(session, "cane-01", False)
    clock.advance(3600)
    assert (await ctx.monitor.run_once()).silent_devices == 0


async def test_threshold_follows_device_period(
    ctx: AppContext, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    async with ctx.db.transaction() as session:
        await ctx.devices.set_period(session, "cane-01", 5)
    await bound_cane.emit()
    clock.advance(16)
    assert (await ctx.monitor.run_once()).silent_devices == 1


async def test_server_restart_gives_devices_time_to_reconnect(
    ctx: AppContext, bound_cane: DeviceClient, clock: FakeClock, tmp_path: Path
) -> None:
    await bound_cane.emit()
    clock.advance(3 * 3600)
    restarted = build_context(make_settings(tmp_path), clock=clock, bot_session=RecordingSession())
    try:
        assert (await restarted.monitor.run_once()).silent_devices == 0
        clock.advance(THRESHOLD_S + 1)
        assert (await restarted.monitor.run_once()).silent_devices == 1
    finally:
        await restarted.db.dispose()


async def test_silence_flag_is_stored_in_database(
    ctx: AppContext, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    await bound_cane.emit()
    clock.advance(THRESHOLD_S + 1)
    await ctx.monitor.run_once()
    async with ctx.db.transaction() as session:
        device = await repositories.get_device(session, "cane-01")
    assert device is not None
    assert device.silence_alerted_at == clock.now()


async def test_each_device_is_checked_separately(
    ctx: AppContext, bound_cane: DeviceClient, clock: FakeClock
) -> None:
    other = await register_device(ctx, "cane-02", "Вторая трость")
    second = DeviceClient(bound_cane.http, other, clock)
    await bound_cane.emit()
    clock.advance(60)
    await second.emit()
    clock.advance(THRESHOLD_S - 59)
    assert (await ctx.monitor.run_once()).silent_devices == 1
