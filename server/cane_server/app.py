import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from fastapi import FastAPI

from cane_server.api import dev_routes, device_routes
from cane_server.api.errors import ApiError, api_error_handler
from cane_server.bot.setup import register_bot_commands
from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.config import ConfigurationError, Settings
from cane_server.context import AppContext, build_context

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, context: AppContext | None = None) -> FastAPI:
    ctx = context or build_context(settings or Settings())
    app = FastAPI(title="Умная трость — сервер", version="1.0.0", lifespan=_lifespan)
    app.state.ctx = ctx
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(device_routes.router)
    recording_session = ctx.recording_session
    if ctx.settings.dev_endpoints_enabled and recording_session is not None:
        app.state.simulated_chat = SimulatedChat(ctx.dispatcher, ctx.bot, recording_session)
        app.include_router(dev_routes.router)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    ctx: AppContext = app.state.ctx
    await ctx.db.create_schema()
    await _prepare_bot(ctx)
    tasks = [_spawn(coroutine, name) for name, coroutine in _background_jobs(ctx)]
    logger.info(
        "Сервер запущен: DRY_RUN=%s, фоновые задачи: %s",
        ctx.settings.dry_run,
        ", ".join(task.get_name() for task in tasks),
    )
    try:
        yield
    finally:
        await _stop(tasks)
        await ctx.bot.session.close()
        await ctx.db.dispose()
        logger.info("Сервер остановлен")


async def _prepare_bot(ctx: AppContext) -> None:
    try:
        await _connect_bot(ctx)
    except TelegramUnauthorizedError as error:
        raise ConfigurationError("Telegram отклонил BOT_TOKEN: проверьте токен") from error
    except TelegramNetworkError as error:
        logger.warning("Telegram сейчас недоступен (%s), бот подключится позже", error)


async def _connect_bot(ctx: AppContext) -> None:
    if not ctx.settings.dry_run:
        await ctx.bot.delete_webhook(drop_pending_updates=False)
        me = await ctx.bot.get_me()
        logger.info("Бот подключён к Telegram: @%s", me.username)
    await register_bot_commands(ctx.bot)


def _background_jobs(ctx: AppContext) -> list[tuple[str, Coroutine[Any, Any, None]]]:
    jobs: list[tuple[str, Coroutine[Any, Any, None]]] = [
        ("outbox", ctx.outbox.run_forever()),
        ("monitor", ctx.monitor.run_forever()),
    ]
    if not ctx.settings.dry_run:
        polling = ctx.dispatcher.start_polling(
            ctx.bot, handle_signals=False, close_bot_session=False
        )
        jobs.append(("telegram-polling", polling))
    return jobs


def _spawn(coroutine: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine, name=name)


async def _stop(tasks: list[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
