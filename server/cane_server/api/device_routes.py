import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.api.device_auth import (
    authenticate_device,
    decode_json,
    read_limited_body,
    validate_packet,
)
from cane_server.api.errors import ApiError
from cane_server.api.schemas import BatchItemResult, BatchReply, DeviceConfig, DeviceReply
from cane_server.storage.models import Device

if TYPE_CHECKING:
    from cane_server.context import AppContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["device"])


def app_context(request: Request) -> "AppContext":
    return request.app.state.ctx


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {"status": "ok", "time": app_context(request).clock.now()}


@router.post("/telemetry", response_model=DeviceReply)
@router.post("/event", response_model=DeviceReply)
async def receive_packet(request: Request) -> DeviceReply:
    ctx = app_context(request)
    body = await read_limited_body(request, ctx.settings.max_body_bytes)
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        device = await authenticate_device(
            session, request.headers, body, ctx.settings.hmac_required
        )
        packet = validate_packet(decode_json(body), device)
        result = await ctx.ingestor.handle(session, device, packet, now)
    ctx.outbox.wake()
    logger.info(
        "Пакет %s seq=%s type=%s buffered=%s%s -> cmd=%s",
        device.id,
        packet.seq,
        packet.type.value,
        packet.buffered,
        " (дубликат)" if result.duplicate else "",
        result.command,
    )
    return DeviceReply(
        ack=result.seq,
        dup=result.duplicate,
        cmd=result.command,
        cfg=DeviceConfig(period_s=result.period_s),
    )


@router.post("/telemetry/batch", response_model=BatchReply)
async def receive_batch(request: Request) -> BatchReply:
    ctx = app_context(request)
    max_bytes = ctx.settings.max_body_bytes * ctx.settings.batch_max_packets
    body = await read_limited_body(request, max_bytes)
    now = ctx.clock.now()
    async with ctx.db.transaction() as session:
        device = await authenticate_device(
            session, request.headers, body, ctx.settings.hmac_required
        )
        items = extract_batch_items(decode_json(body), ctx.settings.batch_max_packets)
        await ctx.ingestor.begin_exchange(session, device, now)
        results = [
            await ingest_batch_item(ctx, session, device, index, item, now)
            for index, item in enumerate(items)
        ]
        command = await ctx.ingestor.finish_exchange(session, device, now)
    ctx.outbox.wake()
    logger.info("Пакет-батч %s: %s записей -> cmd=%s", device.id, len(results), command)
    return BatchReply(results=results, cmd=command, cfg=DeviceConfig(period_s=device.period_s))


def extract_batch_items(data: Any, max_items: int) -> list[Any]:
    items = data.get("packets") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ApiError(422, "validation_error", 'Ожидается объект вида {"packets": [...]}')
    if len(items) > max_items:
        raise ApiError(422, "validation_error", f"Не больше {max_items} пакетов за раз")
    return items


async def ingest_batch_item(
    ctx: "AppContext",
    session: AsyncSession,
    device: Device,
    index: int,
    item: Any,
    now: int,
) -> BatchItemResult:
    raw_seq = item.get("seq") if isinstance(item, dict) else None
    seq = raw_seq if isinstance(raw_seq, int) else None
    try:
        packet = validate_packet(item, device)
    except ApiError as error:
        return BatchItemResult(index=index, seq=seq, status="rejected", error=error.detail)
    accepted = await ctx.ingestor.accept(session, device, packet, now)
    return BatchItemResult(index=index, seq=packet.seq, status="ok" if accepted else "dup")
