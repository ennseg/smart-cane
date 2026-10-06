from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EventType(StrEnum):
    POS = "pos"
    SOS = "sos"
    STILL = "still"
    BATT_LOW = "batt_low"
    BATT_CRIT = "batt_crit"
    GPS_LOST = "gps_lost"
    GPS_OK = "gps_ok"
    SILENT = "silent"
    BACK_ONLINE = "back_online"


class CommandStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    DONE = "done"
    EXPIRED = "expired"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class OutboxMethod(StrEnum):
    TEXT = "text"
    LOCATION = "location"
    LIVE_LOCATION = "live_location"
    EDIT_LIVE = "edit_live"


class BindCodeKind(StrEnum):
    PASSPORT = "passport"
    INVITE = "invite"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hmac_secret: Mapped[str] = mapped_column(String(128))
    period_s: Mapped[int]
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    last_seen_at: Mapped[int | None] = mapped_column(BigInteger)
    last_packet_type: Mapped[str | None] = mapped_column(String(16))
    state_ts: Mapped[int | None] = mapped_column(BigInteger)
    bat_v: Mapped[float | None]
    bat_pct: Mapped[int | None]
    net: Mapped[str | None] = mapped_column(String(8))
    rssi: Mapped[int | None]
    sats: Mapped[int | None]
    hdop: Mapped[float | None]
    fix: Mapped[bool | None]
    silence_alerted_at: Mapped[int | None] = mapped_column(BigInteger)


class Guardian(Base):
    __tablename__ = "guardians"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    display_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[int] = mapped_column(BigInteger)
    bind_failures: Mapped[int] = mapped_column(default=0)
    bind_blocked_until: Mapped[int | None] = mapped_column(BigInteger)


class DeviceGuardian(Base):
    __tablename__ = "device_guardians"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("guardians.chat_id", ondelete="CASCADE"), primary_key=True, index=True
    )
    bound_at: Mapped[int] = mapped_column(BigInteger)


class BindCode(Base):
    __tablename__ = "bind_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    created_by_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int | None] = mapped_column(BigInteger)
    used_at: Mapped[int | None] = mapped_column(BigInteger)
    used_by_chat_id: Mapped[int | None] = mapped_column(BigInteger)


class Packet(Base):
    __tablename__ = "packets"
    __table_args__ = (
        UniqueConstraint("device_id", "seq", name="uq_packets_device_seq"),
        Index("ix_packets_device_received", "device_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(16))
    ts: Mapped[int | None] = mapped_column(BigInteger)
    effective_ts: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[int] = mapped_column(BigInteger)
    buffered: Mapped[bool]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_device_ts", "device_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    packet_id: Mapped[int] = mapped_column(ForeignKey("packets.id", ondelete="CASCADE"))
    ts: Mapped[int] = mapped_column(BigInteger)
    lat: Mapped[float]
    lon: Mapped[float]
    sats: Mapped[int | None]
    hdop: Mapped[float | None]
    net: Mapped[str | None] = mapped_column(String(8))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_device_type_created", "device_id", "type", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    packet_id: Mapped[int | None] = mapped_column(ForeignKey("packets.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(16))
    ts: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    buffered: Mapped[bool] = mapped_column(default=False)
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    notified: Mapped[bool] = mapped_column(default=False)
    suppressed_reason: Mapped[str | None] = mapped_column(String(32))
    acked_by_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    acked_at: Mapped[int | None] = mapped_column(BigInteger)
    reminders_sent: Mapped[int] = mapped_column(default=0)
    next_reminder_at: Mapped[int | None] = mapped_column(BigInteger)
    live_until: Mapped[int | None] = mapped_column(BigInteger)


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    __table_args__ = (Index("ix_device_commands_device_status", "device_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[int] = mapped_column(BigInteger)
    delivered_at: Mapped[int | None] = mapped_column(BigInteger)
    completed_at: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)


class CommandWaiter(Base):
    __tablename__ = "command_waiters"

    command_id: Mapped[int] = mapped_column(
        ForeignKey("device_commands.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class OutboxMessage(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[int] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(String(256))
    sent_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[int | None] = mapped_column(BigInteger)


class LiveLocation(Base):
    __tablename__ = "live_locations"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    live_until: Mapped[int] = mapped_column(BigInteger)
