import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.security import generate_device_token, generate_hmac_secret, sha256_hex
from cane_server.services.binding import BindingService
from cane_server.storage import repositories
from cane_server.storage.models import Device

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class DeviceRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceCredentials:
    device_id: str
    name: str
    token: str
    hmac_secret: str
    period_s: int


@dataclass(frozen=True)
class RegisteredDevice:
    credentials: DeviceCredentials
    bind_code: str


class DeviceRegistry:
    def __init__(self, binding: BindingService) -> None:
        self._binding = binding

    async def register(
        self, session: AsyncSession, device_id: str, name: str, period_s: int, now: int
    ) -> RegisteredDevice:
        _validate_device_id(device_id)
        if period_s < 1:
            raise DeviceRegistryError("Период передачи должен быть не меньше 1 секунды")
        if await repositories.get_device(session, device_id) is not None:
            raise DeviceRegistryError(f"Устройство {device_id} уже зарегистрировано")
        token = generate_device_token()
        secret = generate_hmac_secret()
        session.add(
            Device(
                id=device_id,
                name=name,
                token_hash=sha256_hex(token),
                hmac_secret=secret,
                period_s=period_s,
                enabled=True,
                created_at=now,
            )
        )
        await session.flush()
        issued = self._binding.issue_passport_code(session, device_id, now)
        credentials = DeviceCredentials(device_id, name, token, secret, period_s)
        return RegisteredDevice(credentials=credentials, bind_code=issued.code)

    async def rotate_credentials(self, session: AsyncSession, device_id: str) -> DeviceCredentials:
        device = await self._require(session, device_id)
        token = generate_device_token()
        secret = generate_hmac_secret()
        device.token_hash = sha256_hex(token)
        device.hmac_secret = secret
        return DeviceCredentials(device.id, device.name, token, secret, device.period_s)

    async def issue_passport_code(self, session: AsyncSession, device_id: str, now: int) -> str:
        await self._require(session, device_id)
        return self._binding.issue_passport_code(session, device_id, now).code

    async def set_enabled(self, session: AsyncSession, device_id: str, enabled: bool) -> None:
        device = await self._require(session, device_id)
        device.enabled = enabled

    async def set_period(self, session: AsyncSession, device_id: str, period_s: int) -> None:
        if period_s < 1:
            raise DeviceRegistryError("Период передачи должен быть не меньше 1 секунды")
        device = await self._require(session, device_id)
        device.period_s = period_s

    @staticmethod
    async def _require(session: AsyncSession, device_id: str) -> Device:
        device = await repositories.get_device(session, device_id)
        if device is None:
            raise DeviceRegistryError(f"Устройство {device_id} не найдено")
        return device


def _validate_device_id(device_id: str) -> None:
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise DeviceRegistryError(
            "Идентификатор устройства: 1–32 символа, латиница, цифры, «-» и «_»"
        )
