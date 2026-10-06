import json
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.api.errors import ApiError
from cane_server.api.schemas import TelemetryPacket
from cane_server.security import sha256_hex, signature_matches, token_matches
from cane_server.storage import repositories
from cane_server.storage.models import Device

TOKEN_HEADER = "X-Device-Token"
SIGNATURE_HEADER = "X-Signature"


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    declared_length = request.headers.get("content-length")
    if (
        declared_length is not None
        and declared_length.isdigit()
        and int(declared_length) > max_bytes
    ):
        raise ApiError(413, "body_too_large", f"Тело запроса больше {max_bytes} байт")
    body = await request.body()
    if len(body) > max_bytes:
        raise ApiError(413, "body_too_large", f"Тело запроса больше {max_bytes} байт")
    return body


async def authenticate_device(
    session: AsyncSession, headers: Mapping[str, str], body: bytes, hmac_required: bool
) -> Device:
    token = headers.get(TOKEN_HEADER)
    if not token:
        raise ApiError(401, "invalid_token", f"Нет заголовка {TOKEN_HEADER}")
    device = await repositories.find_device_by_token_hash(session, sha256_hex(token))
    if device is None or not token_matches(device.token_hash, token):
        raise ApiError(401, "invalid_token", "Неизвестный токен устройства")
    if hmac_required:
        _verify_signature(device, headers.get(SIGNATURE_HEADER), body)
    if not device.enabled:
        raise ApiError(403, "device_disabled", "Устройство отключено администратором")
    return device


def _verify_signature(device: Device, signature: str | None, body: bytes) -> None:
    if not signature:
        raise ApiError(401, "invalid_signature", f"Нет заголовка {SIGNATURE_HEADER}")
    if not signature_matches(device.hmac_secret, body, signature):
        raise ApiError(401, "invalid_signature", "Подпись HMAC не совпадает с телом запроса")


def decode_json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ApiError(400, "malformed_json", f"Тело не является JSON: {error}") from error


def validate_packet(data: Any, device: Device) -> TelemetryPacket:
    try:
        packet = TelemetryPacket.model_validate(data)
    except ValidationError as error:
        raise ApiError(422, "validation_error", describe_validation_error(error)) from error
    if packet.dev != device.id:
        raise ApiError(
            403, "dev_mismatch", f"Поле dev={packet.dev} не соответствует токену устройства"
        )
    return packet


def describe_validation_error(error: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in issue["loc"]) or "body",
            "message": issue["msg"],
        }
        for issue in error.errors()
    ]
