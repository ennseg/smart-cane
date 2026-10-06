import itertools
import math
import random
from dataclasses import dataclass

METERS_PER_DEGREE_LAT = 111_320.0
VOLTAGE_TO_PERCENT = [
    (3.30, 0),
    (3.50, 8),
    (3.60, 15),
    (3.70, 30),
    (3.80, 45),
    (4.00, 80),
    (4.20, 100),
]
ITMO_KRONVERKSKY = (59.957155, 30.308288)


def battery_percent(voltage: float) -> int:
    if voltage <= VOLTAGE_TO_PERCENT[0][0]:
        return 0
    for (low_v, low_pct), (high_v, high_pct) in itertools.pairwise(VOLTAGE_TO_PERCENT):
        if voltage <= high_v:
            share = (voltage - low_v) / (high_v - low_v)
            return round(low_pct + share * (high_pct - low_pct))
    return 100


@dataclass
class Walker:
    lat: float = ITMO_KRONVERKSKY[0]
    lon: float = ITMO_KRONVERKSKY[1]
    heading_deg: float = 60.0

    def walk(self, meters: float) -> None:
        self.heading_deg = (self.heading_deg + random.uniform(-20, 20)) % 360
        self._shift(meters, self.heading_deg)

    def jitter(self, meters: float) -> tuple[float, float]:
        angle = random.uniform(0, 360)
        distance = random.uniform(0, meters)
        north = distance * math.cos(math.radians(angle))
        east = distance * math.sin(math.radians(angle))
        return (
            self.lat + north / METERS_PER_DEGREE_LAT,
            self.lon + east / self._meters_per_degree_lon(),
        )

    def _shift(self, meters: float, heading_deg: float) -> None:
        north = meters * math.cos(math.radians(heading_deg))
        east = meters * math.sin(math.radians(heading_deg))
        self.lat += north / METERS_PER_DEGREE_LAT
        self.lon += east / self._meters_per_degree_lon()

    def _meters_per_degree_lon(self) -> float:
        return METERS_PER_DEGREE_LAT * math.cos(math.radians(self.lat))
