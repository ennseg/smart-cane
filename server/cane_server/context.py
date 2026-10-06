from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode

from cane_server.bot.dry_run import RecordingSession
from cane_server.bot.setup import build_dispatcher
from cane_server.clock import Clock, SystemClock
from cane_server.config import Settings
from cane_server.services.alerts import AlertService
from cane_server.services.binding import BindingService
from cane_server.services.commands import CommandService
from cane_server.services.devices import DeviceRegistry
from cane_server.services.ingest import TelemetryIngestor
from cane_server.services.monitor import BackgroundMonitor
from cane_server.services.notifier import Notifier
from cane_server.services.outbox import OutboxDispatcher
from cane_server.services.presence import PresenceService
from cane_server.services.texts import Texts
from cane_server.storage.database import Database


@dataclass
class AppContext:
    settings: Settings
    clock: Clock
    db: Database
    bot: Bot
    dispatcher: Dispatcher
    texts: Texts
    notifier: Notifier
    outbox: OutboxDispatcher
    binding: BindingService
    devices: DeviceRegistry
    commands: CommandService
    alerts: AlertService
    presence: PresenceService
    ingestor: TelemetryIngestor
    monitor: BackgroundMonitor

    @property
    def recording_session(self) -> RecordingSession | None:
        session = self.bot.session
        return session if isinstance(session, RecordingSession) else None


def build_bot(settings: Settings, session: BaseSession | None = None) -> Bot:
    if session is None and settings.dry_run:
        session = RecordingSession()
    return Bot(
        token=settings.effective_bot_token(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_context(
    settings: Settings,
    clock: Clock | None = None,
    bot_session: BaseSession | None = None,
) -> AppContext:
    clock = clock or SystemClock()
    db = Database(settings.database_url)
    bot = build_bot(settings, bot_session)
    texts = Texts(settings.display_tz, settings.map_url_template)
    notifier = Notifier()
    outbox = OutboxDispatcher(db, bot, clock, settings)
    binding = BindingService(
        notifier,
        texts,
        max_failures=settings.bind_max_failures,
        block_min=settings.bind_block_min,
        invite_ttl_h=settings.invite_ttl_h,
    )
    commands = CommandService(notifier, texts, settings.locate_timeout_s)
    alerts = AlertService(settings, notifier, texts)
    presence = PresenceService(notifier, texts, settings.silence_periods, started_at=clock.now())
    monitor = BackgroundMonitor(
        db, clock, presence, alerts, commands, outbox, settings.monitor_interval_s
    )
    context = AppContext(
        settings=settings,
        clock=clock,
        db=db,
        bot=bot,
        dispatcher=build_dispatcher(),
        texts=texts,
        notifier=notifier,
        outbox=outbox,
        binding=binding,
        devices=DeviceRegistry(binding),
        commands=commands,
        alerts=alerts,
        presence=presence,
        ingestor=TelemetryIngestor(presence, commands, alerts),
        monitor=monitor,
    )
    context.dispatcher["ctx"] = context
    return context
