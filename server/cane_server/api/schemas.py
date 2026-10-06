from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SEQ = 2**32 - 1


class PacketType(StrEnum):
    POS = "pos"
    SOS = "sos"
    STILL = "still"
    BATT_LOW = "batt_low"
    BATT_CRIT = "batt_crit"
    GPS_LOST = "gps_lost"
    GPS_OK = "gps_ok"


class NetworkType(StrEnum):
    WIFI = "wifi"
    GSM = "gsm"


class TelemetryPacket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dev: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    seq: int = Field(ge=0, le=MAX_SEQ)
    ts: int | None = Field(default=None, ge=0)
    type: PacketType
    fix: bool = False
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    sats: int | None = Field(default=None, ge=0, le=64)
    hdop: float | None = Field(default=None, ge=0, le=99.9)
    bat_v: float | None = Field(default=None, ge=2.5, le=5.0)
    bat_pct: int | None = Field(default=None, ge=0, le=100)
    net: NetworkType | None = None
    rssi: int | None = Field(default=None, ge=-150, le=0)
    buffered: bool = False
    still_min: int | None = Field(default=None, ge=0, le=1440)

    @model_validator(mode="after")
    def require_coordinates_with_fix(self) -> Self:
        if self.fix and (self.lat is None or self.lon is None):
            raise ValueError("при fix=true поля lat и lon обязательны")
        return self

    @property
    def has_coordinates(self) -> bool:
        if self.lat is None or self.lon is None:
            return False
        return not (self.lat == 0 and self.lon == 0)

    @property
    def has_valid_fix(self) -> bool:
        return self.fix and self.has_coordinates


class DeviceConfig(BaseModel):
    period_s: int


class DeviceReply(BaseModel):
    ack: int
    dup: bool
    cmd: str | None
    cfg: DeviceConfig


class BatchItemResult(BaseModel):
    index: int
    seq: int | None
    status: str
    error: Any = None


class BatchReply(BaseModel):
    results: list[BatchItemResult]
    cmd: str | None
    cfg: DeviceConfig


class ErrorReply(BaseModel):
    error: str
    detail: Any = None
