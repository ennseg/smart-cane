import asyncio
import logging
from dataclasses import dataclass

from cane_server.clock import Clock
from cane_server.services.alerts import AlertService
from cane_server.services.commands import CommandService
from cane_server.services.outbox import OutboxDispatcher
from cane_server.services.presence import PresenceService
from cane_server.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorReport:
    silent_devices: int
    sos_reminders: int
    expired_commands: int

    @property
    def has_news(self) -> bool:
        return any((self.silent_devices, self.sos_reminders, self.expired_commands))


class BackgroundMonitor:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        presence: PresenceService,
        alerts: AlertService,
        commands: CommandService,
        outbox: OutboxDispatcher,
        interval_s: float,
    ) -> None:
        self._db = db
        self._clock = clock
        self._presence = presence
        self._alerts = alerts
        self._commands = commands
        self._outbox = outbox
        self._interval_s = interval_s

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("Ошибка фоновой проверки")
            await asyncio.sleep(self._interval_s)

    async def run_once(self) -> MonitorReport:
        now = self._clock.now()
        async with self._db.transaction() as session:
            report = MonitorReport(
                silent_devices=await self._presence.check_silent_devices(session, now),
                sos_reminders=await self._alerts.send_due_sos_reminders(session, now),
                expired_commands=await self._commands.expire_overdue(session, now),
            )
        if report.has_news:
            logger.info("Фоновая проверка: %s", report)
            self._outbox.wake()
        return report
