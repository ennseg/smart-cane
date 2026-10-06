import time
from collections.abc import Callable

from simulator.device import VirtualCane, encode

WALK_STEP_M = 40
SOS_MODE_POSITIONS = 6
SOS_MODE_SPEEDUP = 2


class ScenarioRunner:
    def __init__(
        self,
        cane: VirtualCane,
        log: Callable[[str], None],
        sleep: Callable[[float], None] = time.sleep,
        crit_sleep_s: float | None = None,
    ) -> None:
        self.cane = cane
        self._log = log
        self._sleep = sleep
        self._crit_sleep_s = crit_sleep_s

    def pause(self, periods: float = 1) -> None:
        self._sleep(self.cane.period_s * periods)

    def tick(self, moving: bool = True) -> None:
        if moving:
            self.cane.state.walker.walk(WALK_STEP_M)
        self.cane.emit("pos")
        self.pause()

    def title(self, text: str) -> None:
        self._log(f"\n=== {text} ===")

    def walk(self, steps: int = 6) -> None:
        self.title("Обычная ходьба: плановая позиция каждый период")
        for _ in range(steps):
            self.tick()

    def sos(self) -> None:
        self.title("SOS: удержание кнопки ≥ 2 с")
        packet = self.cane.emit("sos")
        self._log("   имитирую потерю ответа: повторяю тот же пакет SOS (тот же seq)")
        self.cane.deliver(packet)
        self._log("   режим SOS: позиция в 2 раза чаще обычного")
        for _ in range(SOS_MODE_POSITIONS):
            self.cane.state.walker.walk(WALK_STEP_M / 4)
            self.cane.emit("pos")
            self._sleep(self.cane.period_s / SOS_MODE_SPEEDUP)

    def still(self) -> None:
        self.title("Неподвижность: человек стоит на месте")
        for _ in range(3):
            self.tick(moving=False)
        self.cane.emit("still", still_min=10)
        self.pause()
        self.tick(moving=False)
        self._log("   напоминание устройства (в реальности через 30 мин)")
        self.cane.emit("still", still_min=40)
        self.pause()
        self._log("   человек пошёл дальше")
        self.tick()

    def battery(self) -> None:
        self.title("Разряд батареи: предупреждение, критический уровень, глубокий сон")
        state = self.cane.state
        for voltage in (3.75, 3.66):
            state.bat_v = voltage
            self.tick()
        state.bat_v = 3.58
        self.cane.emit("batt_low")
        self.pause()
        state.bat_v = 3.52
        self.tick()
        state.bat_v = 3.44
        self.cane.emit("batt_crit")
        sleep_s = self._crit_sleep_s if self._crit_sleep_s is not None else self.cane.period_s * 5
        self._log(f"   глубокий сон: устройство молчит {sleep_s:.0f} с")
        self._sleep(sleep_s)
        self._log("   трость поставили на зарядку и включили")
        state.bat_v = 4.10
        self.tick()

    def gps_loss(self) -> None:
        self.title("Потеря и восстановление GPS")
        self.tick()
        state = self.cane.state
        state.fix, state.sats, state.hdop = False, 2, 9.9
        self._log("   человек зашёл в помещение: фикса нет")
        self.tick(moving=False)
        self.cane.emit("gps_lost")
        self.pause()
        self.tick(moving=False)
        state.fix, state.sats, state.hdop = True, 7, 1.4
        self._log("   человек вышел на улицу: фикс снова есть")
        self.cane.emit("gps_ok")
        self.pause()
        self.tick()

    def offline(self, shuffle: bool = False) -> None:
        self.title("Нет связи: запись в OfflineBuffer и досылка")
        self.tick()
        self.cane.state.online = False
        for _ in range(3):
            self.tick()
        self.cane.emit("still", still_min=12)
        self.tick(moving=False)
        self._log("   связь восстановлена")
        self.cane.state.online = True
        self.cane.flush_buffer(shuffle=shuffle)
        self.tick()

    def errors(self) -> None:
        self.title("Ошибочные запросы: сервер должен их отклонить")
        cane = self.cane
        packet = cane.build_packet("pos")
        body = encode(packet)
        good_headers = cane.auth_headers(body)
        self._show("неверный токен", cane.post(body, {**good_headers, "X-Device-Token": "wrong"}))
        self._show("неверная подпись", cane.post(body, {**good_headers, "X-Signature": "0" * 64}))
        broken = b'{"dev": "oops"'
        self._show("битый JSON", cane.post(broken, cane.auth_headers(broken)))
        invalid = encode({**packet, "seq": packet["seq"] + 1000, "lat": 200})
        self._show("широта 200°", cane.post(invalid, cane.auth_headers(invalid)))
        self._show("корректный пакет", cane.post(body, good_headers))
        self._show("тот же пакет ещё раз", cane.post(body, good_headers))

    def _show(self, label: str, response: object) -> None:
        status = getattr(response, "status_code", "?")
        text = getattr(response, "text", "")
        self._log(f"   {label}: {status} {text}")

    def serve(self) -> None:
        self.title("Бесконечная ходьба (Ctrl+C для выхода); /where в боте будет выполнен")
        while True:
            self.tick()

    def run_all(self, shuffle: bool) -> None:
        self.walk(steps=4)
        self.gps_loss()
        self.still()
        self.sos()
        self.offline(shuffle=shuffle)
        self.errors()
        self.battery()
