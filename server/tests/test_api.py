import json

import pytest
from sqlalchemy import func, select

from cane_server.bot.dry_run import RecordingSession
from cane_server.context import AppContext
from cane_server.security import sign_body
from cane_server.storage import repositories
from cane_server.storage.models import Event, Packet, Position
from tests.conftest import GUARDIAN_A, DeviceClient, FakeClock, delivered, texts_to


async def count(ctx: AppContext, model: type) -> int:
    async with ctx.db.transaction() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_health(cane: DeviceClient) -> None:
    response = await cane.http.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_valid_telemetry_is_stored_and_acknowledged(
    ctx: AppContext, cane: DeviceClient
) -> None:
    response = await cane.send(cane.packet())

    assert response.status_code == 200
    assert response.json() == {"ack": 1, "dup": False, "cmd": None, "cfg": {"period_s": 30}}
    async with ctx.db.transaction() as session:
        position = await repositories.latest_position(session, "cane-01")
        device = await repositories.get_device(session, "cane-01")
    assert position is not None
    assert (position.lat, position.lon) == (59.957155, 30.308288)
    assert device is not None
    assert device.bat_pct == 52
    assert device.net == "wifi"
    assert device.last_seen_at == ctx.clock.now()


async def test_event_endpoint_is_alias_of_telemetry(cane: DeviceClient) -> None:
    response = await cane.send(cane.packet(), path="/api/v1/event")
    assert response.status_code == 200
    assert response.json()["ack"] == 1


async def test_missing_token_is_rejected(ctx: AppContext, cane: DeviceClient) -> None:
    response = await cane.send_raw(json.dumps(cane.packet()).encode(), omit=("X-Device-Token",))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert await count(ctx, Packet) == 0


async def test_wrong_token_is_rejected(ctx: AppContext, cane: DeviceClient) -> None:
    response = await cane.send_raw(json.dumps(cane.packet()).encode(), token="not-a-token")
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert await count(ctx, Packet) == 0


async def test_wrong_signature_is_rejected(ctx: AppContext, cane: DeviceClient) -> None:
    response = await cane.send_raw(json.dumps(cane.packet()).encode(), signature="ab" * 32)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_signature"
    assert await count(ctx, Packet) == 0


async def test_missing_signature_is_rejected(cane: DeviceClient) -> None:
    response = await cane.send_raw(json.dumps(cane.packet()).encode(), omit=("X-Signature",))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_signature"


async def test_signature_of_other_body_is_rejected(cane: DeviceClient) -> None:
    original = json.dumps(cane.packet()).encode()
    tampered = original.replace(b"59.957155", b"55.000000")
    signature = sign_body(cane.credentials.hmac_secret, original)
    response = await cane.send_raw(tampered, signature=signature)
    assert response.status_code == 401


async def test_uppercase_signature_is_accepted(cane: DeviceClient) -> None:
    body = json.dumps(cane.packet()).encode()
    signature = sign_body(cane.credentials.hmac_secret, body).upper()
    response = await cane.send_raw(body, signature=signature)
    assert response.status_code == 200


async def test_signature_is_optional_when_disabled(ctx: AppContext, cane: DeviceClient) -> None:
    ctx.settings.hmac_required = False
    response = await cane.send_raw(json.dumps(cane.packet()).encode(), omit=("X-Signature",))
    assert response.status_code == 200


async def test_malformed_json_is_rejected(cane: DeviceClient) -> None:
    response = await cane.send_raw(b'{"dev": "cane-01", "seq": ')
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"lat": 91.0}, "lat"),
        ({"lon": -181.0}, "lon"),
        ({"type": "teleport"}, "type"),
        ({"seq": -1}, "seq"),
        ({"bat_pct": 101}, "bat_pct"),
        ({"net": "lte"}, "net"),
        ({"fix": True, "lat": None}, "body"),
    ],
)
async def test_invalid_fields_are_rejected(
    cane: DeviceClient, overrides: dict[str, object], field: str
) -> None:
    response = await cane.send(cane.packet(**overrides))
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert any(field in issue["field"] for issue in body["detail"])


async def test_non_object_json_is_rejected(cane: DeviceClient) -> None:
    response = await cane.send_raw(b"[1, 2, 3]")
    assert response.status_code == 422


async def test_dev_must_match_token(cane: DeviceClient) -> None:
    response = await cane.send(cane.packet(dev="cane-99"))
    assert response.status_code == 403
    assert response.json()["error"] == "dev_mismatch"


async def test_disabled_device_is_rejected(ctx: AppContext, cane: DeviceClient) -> None:
    async with ctx.db.transaction() as session:
        await ctx.devices.set_enabled(session, "cane-01", False)
    response = await cane.send(cane.packet())
    assert response.status_code == 403
    assert response.json()["error"] == "device_disabled"


async def test_too_large_body_is_rejected(cane: DeviceClient) -> None:
    response = await cane.send(cane.packet(padding="x" * 5000))
    assert response.status_code == 413


async def test_unknown_extra_fields_are_ignored(cane: DeviceClient) -> None:
    response = await cane.send(cane.packet(firmware="1.2.3"))
    assert response.status_code == 200


async def test_duplicate_seq_is_acknowledged_without_reprocessing(
    ctx: AppContext, bound_cane: DeviceClient, telegram: RecordingSession
) -> None:
    sos = bound_cane.packet("sos")
    first = await bound_cane.send(sos)
    second = await bound_cane.send(sos)

    assert first.json()["dup"] is False
    assert second.status_code == 200
    assert second.json() == {"ack": sos["seq"], "dup": True, "cmd": None, "cfg": {"period_s": 30}}
    assert await count(ctx, Packet) == 1
    assert await count(ctx, Event) == 1
    calls = await delivered(ctx, telegram)
    assert len([text for text in texts_to(calls, GUARDIAN_A) if "SOS" in text]) == 1


async def test_buffered_packets_out_of_order(
    ctx: AppContext, cane: DeviceClient, clock: FakeClock
) -> None:
    base = clock.now()
    clock.advance(600)
    packets = [
        cane.packet(
            ts=base + 60 * index, lat=59.95 + index / 1000, bat_pct=60 - index, buffered=True
        )
        for index in range(1, 6)
    ]
    for packet in [packets[4], packets[2], packets[0], packets[3], packets[1]]:
        response = await cane.send(packet)
        assert response.status_code == 200
        assert response.json()["ack"] == packet["seq"]

    assert await count(ctx, Position) == 5
    async with ctx.db.transaction() as session:
        latest = await repositories.latest_position(session, "cane-01")
        device = await repositories.get_device(session, "cane-01")
        history = await repositories.recent_positions(session, "cane-01", 10)
    assert latest is not None
    assert latest.ts == base + 300
    assert device is not None
    assert device.bat_pct == 55
    assert device.state_ts == base + 300
    assert [position.ts for position in history] == [base + 60 * i for i in range(5, 0, -1)]


async def test_old_buffered_packet_does_not_override_fresh_state(
    ctx: AppContext, cane: DeviceClient, clock: FakeClock
) -> None:
    await cane.emit(bat_pct=40, net="gsm")
    await cane.emit(ts=clock.now() - 900, bat_pct=90, net="wifi", buffered=True)
    async with ctx.db.transaction() as session:
        device = await repositories.get_device(session, "cane-01")
    assert device is not None
    assert device.bat_pct == 40
    assert device.net == "gsm"


@pytest.mark.parametrize("bad_ts", [None, 0, 1_000, 4_000_000_000])
async def test_untrusted_device_time_is_replaced_by_server_time(
    ctx: AppContext, cane: DeviceClient, bad_ts: int | None
) -> None:
    await cane.emit(ts=bad_ts)
    async with ctx.db.transaction() as session:
        position = await repositories.latest_position(session, "cane-01")
    assert position is not None
    assert position.ts == ctx.clock.now()


async def test_packet_without_fix_does_not_create_position(
    ctx: AppContext, cane: DeviceClient
) -> None:
    await cane.emit(fix=False, lat=None, lon=None, sats=2)
    await cane.emit(fix=True, lat=0.0, lon=0.0)
    assert await count(ctx, Position) == 0
    assert await count(ctx, Packet) == 2


async def test_reply_contains_locate_now_after_where(
    ctx: AppContext, bound_cane: DeviceClient
) -> None:
    async with ctx.db.transaction() as session:
        await ctx.commands.request_location(session, "cane-01", GUARDIAN_A, ctx.clock.now())
    first = await bound_cane.emit()
    assert first["cmd"] == "locate_now"
    second = await bound_cane.emit()
    assert second["cmd"] is None


async def test_locate_now_is_repeated_until_fresh_position(
    ctx: AppContext, bound_cane: DeviceClient
) -> None:
    async with ctx.db.transaction() as session:
        await ctx.commands.request_location(session, "cane-01", GUARDIAN_A, ctx.clock.now())
    assert (await bound_cane.emit())["cmd"] == "locate_now"
    assert (await bound_cane.emit(buffered=True, ts=ctx.clock.now() - 100))["cmd"] == "locate_now"
    assert (await bound_cane.emit())["cmd"] is None


async def test_period_from_database_is_sent_in_cfg(ctx: AppContext, cane: DeviceClient) -> None:
    async with ctx.db.transaction() as session:
        await ctx.devices.set_period(session, "cane-01", 15)
    reply = await cane.emit()
    assert reply["cfg"] == {"period_s": 15}


async def test_batch_accepts_valid_reports_duplicates_and_rejects_invalid(
    ctx: AppContext, cane: DeviceClient
) -> None:
    first = cane.packet(buffered=True)
    batch = {
        "packets": [
            first,
            first,
            cane.packet(lat=500.0),
            cane.packet("still", still_min=12, buffered=True),
            "garbage",
        ]
    }
    response = await cane.send(batch, path="/api/v1/telemetry/batch")

    assert response.status_code == 200
    statuses = [(item["index"], item["status"]) for item in response.json()["results"]]
    assert statuses == [(0, "ok"), (1, "dup"), (2, "rejected"), (3, "ok"), (4, "rejected")]
    assert response.json()["cfg"] == {"period_s": 30}
    assert await count(ctx, Packet) == 2


async def test_batch_requires_packets_list(cane: DeviceClient) -> None:
    response = await cane.send({"items": []}, path="/api/v1/telemetry/batch")
    assert response.status_code == 422


async def test_batch_is_authenticated(cane: DeviceClient) -> None:
    body = json.dumps({"packets": [cane.packet()]}).encode()
    response = await cane.send_raw(body, path="/api/v1/telemetry/batch", token="bad")
    assert response.status_code == 401


async def test_dev_chat_endpoint_talks_to_bot(cane: DeviceClient) -> None:
    response = await cane.http.post("/dev/bot", json={"chat_id": 5, "text": "/help"})
    assert response.status_code == 200
    replies = response.json()["replies"]
    assert replies[0]["method"] == "sendMessage"
    assert "/where" in replies[0]["params"]["text"]


async def test_dev_chat_requires_text_or_button(cane: DeviceClient) -> None:
    response = await cane.http.post("/dev/bot", json={"chat_id": 5})
    assert response.status_code == 422
