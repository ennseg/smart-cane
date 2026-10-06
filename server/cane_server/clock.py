import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> int: ...


class SystemClock:
    def now(self) -> int:
        return int(time.time())
