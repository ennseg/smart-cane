import asyncio
from pathlib import Path

import httpx
import pytest

from cane_server.app import create_app
from cane_server.bot.dry_run import RecordingSession
from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.config import ConfigurationError, Settings
from cane_server.context import AppContext, build_context
from tests.conftest import GUARDIAN_A, DeviceClient, FakeClock, make_settings, texts_to


async def wait_for_texts(
    telegram: RecordingSession, chat_id: int, timeout_s: float = 5
) -> list[str]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        texts = texts_to(telegram.calls, chat_id)
        if texts:
            return texts
        await asyncio.sleep(0.05)
    return []


async def test_lifespan_runs_background_delivery(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    app = create_app(context=ctx)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            bound_cane.http = client
            await bound_cane.emit("sos")
            texts = await wait_for_texts(telegram, GUARDIAN_A)
    assert any("SOS!" in text for text in texts)
    assert any(call.method == "setMyCommands" for call in telegram.calls)


async def test_lifespan_runs_silence_monitor(tmp_path: Path, clock: FakeClock) -> None:
    settings = make_settings(tmp_path, monitor_interval_s=0.05)
    context = build_context(settings, clock=clock, bot_session=RecordingSession())
    app = create_app(context=context)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with context.db.transaction() as session:
                registered = await context.devices.register(
                    session, "cane-01", "Трость", 30, clock.now()
                )
            cane = DeviceClient(client, registered, clock)
            telegram = context.recording_session
            assert telegram is not None
            chat = SimulatedChat(context.dispatcher, context.bot, telegram)
            await chat.send_text(GUARDIAN_A, f"/bind {registered.bind_code}")
            await cane.emit()
            clock.advance(91)
            deadline = asyncio.get_running_loop().time() + 5
            while not any("не выходит на связь" in t for t in texts_to(telegram.calls, GUARDIAN_A)):
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)


def test_real_mode_requires_bot_token(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, dry_run=False, bot_token="")
    with pytest.raises(ConfigurationError):
        build_context(settings)


def test_dev_endpoint_is_absent_outside_dry_run(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, dry_run=False, bot_token="123:ABC")
    app = create_app(context=build_context(settings))
    assert "/dev/bot" not in app.openapi()["paths"]


def test_dev_endpoints_flag_defaults_to_dry_run() -> None:
    assert Settings(_env_file=None, dry_run=True).dev_endpoints_enabled
    assert not Settings(_env_file=None, dry_run=False).dev_endpoints_enabled
    assert not Settings(_env_file=None, dry_run=True, dev_endpoints=False).dev_endpoints_enabled


def test_env_example_is_valid_configuration() -> None:
    project_root = Path(__file__).resolve().parent.parent
    settings = Settings(_env_file=project_root / ".env.example")
    assert settings.dry_run
    assert settings.dev_endpoints_enabled
    assert settings.default_period_s == 30
    assert "{lat}" in settings.map_url_template
