from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from cane_server.services.notifier import MapPoint
from cane_server.storage.models import Device, Position

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400

NETWORK_NAMES = {"wifi": "Wi-Fi", "gsm": "GSM"}

HELP_TEXT = (
    "Я бот умной трости. Сообщаю о тревогах и показываю, где находится "
    "человек с тростью.\n\n"
    "<b>Команды</b>\n"
    "/bind КОД — привязаться к трости по коду из паспорта изделия\n"
    "/where — где сейчас трость (последняя позиция + запрос свежей)\n"
    "/status — заряд, связь, спутники, последний контакт\n"
    "/history — последние точки маршрута\n"
    "/invite — код для ещё одного сопровождающего\n"
    "/unbind — отвязаться от трости\n"
    "/help — эта справка\n\n"
    "Тревоги (SOS, неподвижность, разряд, потеря GPS, отсутствие связи) "
    "приходят автоматически."
)


@dataclass(frozen=True)
class Place:
    point: MapPoint | None
    ts: int | None
    is_fix: bool


@dataclass(frozen=True)
class Battery:
    voltage: float | None
    percent: int | None


def format_decimal(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def format_ago(seconds: int) -> str:
    seconds = max(seconds, 0)
    if seconds < SECONDS_PER_MINUTE:
        return "только что"
    if seconds < SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_MINUTE} мин назад"
    if seconds < SECONDS_PER_DAY:
        hours, rest = divmod(seconds, SECONDS_PER_HOUR)
        minutes = rest // SECONDS_PER_MINUTE
        return f"{hours} ч {minutes} мин назад" if minutes else f"{hours} ч назад"
    return f"{seconds // SECONDS_PER_DAY} дн назад"


def format_duration(seconds: int) -> str:
    seconds = max(seconds, 0)
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds} с"
    if seconds < SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_MINUTE} мин"
    hours, rest = divmod(seconds, SECONDS_PER_HOUR)
    minutes = rest // SECONDS_PER_MINUTE
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def format_battery(battery: Battery) -> str:
    if battery.percent is None and battery.voltage is None:
        return "🔋 заряд: нет данных"
    parts = []
    if battery.percent is not None:
        parts.append(f"{battery.percent} %")
    if battery.voltage is not None:
        parts.append(f"({format_decimal(battery.voltage, 2)} В)")
    return "🔋 заряд " + " ".join(parts)


def device_battery(device: Device) -> Battery:
    return Battery(voltage=device.bat_v, percent=device.bat_pct)


class Texts:
    def __init__(self, display_tz: str, map_url_template: str) -> None:
        self._zone = ZoneInfo(display_tz)
        self._map_url_template = map_url_template

    def clock(self, ts: int, now: int) -> str:
        moment = datetime.fromtimestamp(ts, self._zone)
        today = datetime.fromtimestamp(now, self._zone).date()
        if moment.date() == today:
            return moment.strftime("%H:%M")
        return moment.strftime("%d.%m %H:%M")

    def moment(self, ts: int, now: int) -> str:
        return f"{self.clock(ts, now)}, {format_ago(now - ts)}"

    def map_link(self, lat: float, lon: float) -> str:
        url = self._map_url_template.format(lat=f"{lat:.6f}", lon=f"{lon:.6f}")
        return f'<a href="{escape(url)}">{lat:.5f}, {lon:.5f}</a>'

    def delayed_note(self, event_ts: int, now: int) -> str:
        return (
            f"\n⏳ Событие произошло в {self.clock(event_ts, now)} "
            f"({format_ago(now - event_ts)}), получено с задержкой: не было связи."
        )

    def sos_alert(
        self,
        device: Device,
        event_ts: int,
        battery: Battery,
        place: Place,
        now: int,
    ) -> str:
        lines = [
            f"🆘 <b>SOS! {escape(device.name)} просит помощи</b>",
            f"Время: {self.clock(event_ts, now)}",
            format_battery(battery),
            self._sos_position_line(place, now),
        ]
        lines.append("Нажмите «✅ Принял», если вы займётесь тревогой.")
        return "\n".join(lines)

    @staticmethod
    def _sos_position_line(place: Place, now: int) -> str:
        if place.is_fix:
            return "📍 Место — на карте ниже. Во время тревоги карта обновляется."
        if place.point is None:
            return "⚠️ GPS-фикса нет, координаты неизвестны."
        if place.ts is None:
            return "⚠️ GPS-фикса нет. На карте — последние координаты, переданные тростью."
        return (
            "⚠️ GPS-фикса нет. На карте — последняя известная позиция "
            f"({format_ago(now - place.ts)})."
        )

    def sos_reminder(self, device: Device, event_ts: int, number: int, now: int) -> str:
        return (
            f"🆘 <b>Напоминание #{number}: тревога SOS от {escape(device.name)}</b> "
            f"в {self.clock(event_ts, now)} ещё никем не принята."
        )

    def sos_acked_by(self, device: Device, guardian_name: str) -> str:
        return f"✅ Тревогу SOS от {escape(device.name)} принял(а) {escape(guardian_name)}."

    @staticmethod
    def still_alert(device: Device, duration: str, battery: Battery) -> str:
        return (
            f"🧍 <b>{escape(device.name)}: нет движения {duration}</b>\n"
            f"Человек остаётся на одном месте. Позиция — на карте ниже.\n"
            f"{format_battery(battery)}"
        )

    def batt_low_alert(self, device: Device, battery: Battery) -> str:
        return (
            f"🪫 <b>{escape(device.name)}: низкий заряд</b>\n"
            f"{format_battery(battery)}\nПора поставить трость на зарядку."
        )

    def batt_crit_alert(self, device: Device, battery: Battery) -> str:
        return (
            f"🔴 <b>{escape(device.name)}: критический разряд</b>\n"
            f"{format_battery(battery)}\n"
            "Трость отключается для защиты аккумулятора. Последняя позиция — на карте ниже."
        )

    def gps_lost_alert(self, device: Device, place: Place, battery: Battery, now: int) -> str:
        where = self._last_coordinates_note(place, now)
        return (
            f"📡 <b>{escape(device.name)}: пропал сигнал GPS</b>\n"
            f"Возможно, человек в помещении или тоннеле. {where}\n{format_battery(battery)}"
        )

    @staticmethod
    def _last_coordinates_note(place: Place, now: int) -> str:
        if place.point is None:
            return "Координаты ещё ни разу не были получены."
        if place.ts is None:
            return "Последние координаты — на карте ниже."
        return f"Последние координаты ({format_ago(now - place.ts)}) — на карте ниже."

    def gps_ok_alert(self, device: Device) -> str:
        return f"🛰 <b>{escape(device.name)}: сигнал GPS восстановлен</b>\nТекущая позиция ниже."

    def silent_alert(
        self, device: Device, silent_seconds: int, position_ts: int | None, now: int
    ) -> str:
        lines = [
            f"⚠️ <b>{escape(device.name)} не выходит на связь</b> "
            f"уже {format_duration(silent_seconds)}.",
        ]
        if device.last_packet_type == "batt_crit":
            lines.append(
                "Последним пришло сообщение о критическом разряде — вероятно, трость выключилась."
            )
        else:
            lines.append("Возможны проблемы со связью или питанием.")
        if position_ts is not None:
            lines.append(f"Последняя позиция ({format_ago(now - position_ts)}) — на карте ниже.")
        return "\n".join(lines)

    def back_online(self, device: Device, offline_seconds: int) -> str:
        return (
            f"✅ Связь с {escape(device.name)} восстановлена "
            f"(не было {format_duration(offline_seconds)})."
        )

    def where_reply(
        self,
        device: Device,
        position: Position | None,
        already_waiting: bool,
        now: int,
    ) -> str:
        lines = [self._where_position_line(device, position, now)]
        if device.silence_alerted_at is not None and device.last_seen_at is not None:
            lines.append(
                "⚠️ Трость сейчас не выходит на связь (последний контакт "
                f"{format_ago(now - device.last_seen_at)}): свежая позиция придёт, "
                "когда связь восстановится."
            )
        if already_waiting:
            lines.append("Запрос свежей позиции уже отправлен — пришлю, как только она придёт.")
        else:
            lines.append(
                f"Запросил свежую позицию — пришлю примерно через {device.period_s} с "
                "(после очередного выхода трости на связь)."
            )
        return "\n".join(lines)

    def _where_position_line(self, device: Device, position: Position | None, now: int) -> str:
        if position is None:
            return (
                f"📍 <b>{escape(device.name)}</b>: позиция пока неизвестна — "
                "трость ещё не передавала координаты."
            )
        details = f", спутников: {position.sats}" if position.sats is not None else ""
        return (
            f"📍 <b>{escape(device.name)}</b>: последняя позиция "
            f"<b>{format_ago(now - position.ts)}</b> ({self.clock(position.ts, now)}{details})."
        )

    def fresh_position(self, device: Device, position: Position, now: int) -> str:
        return (
            f"📍 <b>Свежая позиция: {escape(device.name)}</b> "
            f"({format_ago(now - position.ts)}, {self.clock(position.ts, now)})."
        )

    def locate_without_fix(self, device: Device) -> str:
        return (
            f"📡 {escape(device.name)} ответила на запрос, но спутники сейчас не видны "
            "(возможно, человек в помещении). Последняя известная позиция — в предыдущем сообщении."
        )

    def locate_timeout(self, device: Device, timeout_s: int) -> str:
        return (
            f"⌛ {escape(device.name)} не ответила на запрос позиции "
            f"за {format_duration(timeout_s)}. Последняя известная позиция — в ответе на /where."
        )

    def status_reply(self, device: Device, now: int) -> str:
        title = f"ℹ️ <b>{escape(device.name)}</b>"
        if device.last_seen_at is None:
            return f"{title}\nТрость ещё ни разу не выходила на связь."
        lines = [
            title,
            format_battery(device_battery(device)),
            self._network_line(device),
            self._gps_line(device),
            f"🕒 последний контакт: {self.moment(device.last_seen_at, now)}",
            f"⏱ период передачи: {device.period_s} с",
        ]
        if device.silence_alerted_at is not None:
            lines.append("⚠️ трость сейчас не выходит на связь")
        return "\n".join(lines)

    def _network_line(self, device: Device) -> str:
        if device.net is None:
            return "📶 сеть: нет данных"
        name = NETWORK_NAMES.get(device.net, device.net)
        signal = f", {device.rssi} dBm" if device.rssi is not None else ""
        return f"📶 сеть: {name}{signal}"

    def _gps_line(self, device: Device) -> str:
        satellites = f"{device.sats} спутников" if device.sats is not None else "спутники: н/д"
        hdop = f", HDOP {format_decimal(device.hdop, 1)}" if device.hdop is not None else ""
        state = "фикс есть" if device.fix else "фикса нет"
        return f"🛰 GPS: {state}, {satellites}{hdop}"

    def history_reply(self, device: Device, positions: Sequence[Position], now: int) -> str:
        if not positions:
            return f"🗺 <b>{escape(device.name)}</b>: точек маршрута пока нет."
        lines = [f"🗺 <b>{escape(device.name)}</b>: последние {len(positions)} точек"]
        for number, position in enumerate(positions, start=1):
            lines.append(
                f"{number}. {self.clock(position.ts, now)} · {format_ago(now - position.ts)} · "
                f"{self.map_link(position.lat, position.lon)}"
            )
        return "\n".join(lines)

    def start_text(self, is_bound: bool) -> str:
        greeting = "Здравствуйте! "
        if is_bound:
            return greeting + HELP_TEXT
        return (
            greeting
            + HELP_TEXT
            + "\n\nЧтобы начать, отправьте /bind и код из паспорта трости, например: "
            "<code>/bind ABCD-EFGH</code>"
        )

    @staticmethod
    def help_text() -> str:
        return HELP_TEXT

    @staticmethod
    def not_bound() -> str:
        return (
            "Вы пока не привязаны ни к одной трости. Отправьте /bind и код из паспорта "
            "изделия, например: <code>/bind ABCD-EFGH</code>"
        )

    @staticmethod
    def bind_usage() -> str:
        return "Укажите код после команды, например: <code>/bind ABCD-EFGH</code>"

    @staticmethod
    def bind_success(device: Device, guardians_count: int) -> str:
        return (
            f"✅ Вы привязаны к трости «{escape(device.name)}». "
            f"Сопровождающих у трости: {guardians_count}.\n"
            "Теперь вам будут приходить тревоги. Попробуйте /where или /status."
        )

    @staticmethod
    def bind_already(device: Device) -> str:
        return f"Вы уже привязаны к трости «{escape(device.name)}»."

    @staticmethod
    def bind_unknown_code() -> str:
        return "❌ Код не найден. Проверьте код из паспорта трости и попробуйте ещё раз."

    @staticmethod
    def bind_used_code() -> str:
        return (
            "❌ Этот код уже использован. Попросите уже привязанного сопровождающего "
            "выполнить /invite, чтобы получить новый код."
        )

    @staticmethod
    def bind_expired_code() -> str:
        return "❌ Срок действия кода истёк. Попросите выдать новый код через /invite."

    def bind_blocked(self, blocked_until: int, now: int) -> str:
        minutes = max(1, (blocked_until - now + 59) // SECONDS_PER_MINUTE)
        return f"⛔ Слишком много неверных попыток. Повторите через {minutes} мин."

    @staticmethod
    def guardian_joined(device: Device, guardian_name: str) -> str:
        return (
            f"👤 К трости «{escape(device.name)}» подключился новый сопровождающий: "
            f"{escape(guardian_name)}."
        )

    def invite_code(self, device: Device, code: str, expires_at: int, now: int) -> str:
        return (
            f"🔑 Код приглашения для трости «{escape(device.name)}»: <code>{code}</code>\n"
            f"Действует до {self.clock(expires_at, now)}, только один раз.\n"
            f"Второй сопровождающий должен отправить боту: <code>/bind {code}</code>"
        )

    @staticmethod
    def unbind_choose() -> str:
        return "От какой трости отвязаться? После отвязки тревоги приходить перестанут."

    @staticmethod
    def unbind_done(device: Device) -> str:
        return f"Вы отвязаны от трости «{escape(device.name)}». Тревоги больше не придут."

    @staticmethod
    def unbind_not_bound() -> str:
        return "Вы уже не привязаны к этой трости."

    @staticmethod
    def unknown_command() -> str:
        return "Не понимаю это сообщение. Список команд — /help"
