import secrets
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from cane_server.security import sha256_hex
from cane_server.services.notifier import Notification, Notifier
from cane_server.services.texts import Texts
from cane_server.storage import repositories
from cane_server.storage.models import BindCode, BindCodeKind, Device, DeviceGuardian, Guardian

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_GROUP = 4
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class BindOutcome(StrEnum):
    BOUND = "bound"
    ALREADY_BOUND = "already_bound"
    UNKNOWN_CODE = "unknown_code"
    USED_CODE = "used_code"
    EXPIRED_CODE = "expired_code"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class BindResult:
    outcome: BindOutcome
    device: Device | None = None
    guardians_count: int = 0
    blocked_until: int | None = None


@dataclass(frozen=True)
class IssuedCode:
    code: str
    expires_at: int | None


def generate_bind_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:CODE_GROUP]}-{raw[CODE_GROUP:]}"


def normalize_bind_code(raw_code: str) -> str:
    return "".join(character for character in raw_code.upper() if character.isalnum())


def hash_bind_code(raw_code: str) -> str:
    return sha256_hex(normalize_bind_code(raw_code))


class BindingService:
    def __init__(
        self,
        notifier: Notifier,
        texts: Texts,
        max_failures: int,
        block_min: int,
        invite_ttl_h: int,
    ) -> None:
        self._notifier = notifier
        self._texts = texts
        self._max_failures = max_failures
        self._block_s = block_min * SECONDS_PER_MINUTE
        self._invite_ttl_s = invite_ttl_h * SECONDS_PER_HOUR

    def issue_passport_code(self, session: AsyncSession, device_id: str, now: int) -> IssuedCode:
        return self._issue_code(session, device_id, BindCodeKind.PASSPORT, None, None, now)

    def issue_invite_code(
        self, session: AsyncSession, device_id: str, chat_id: int, now: int
    ) -> IssuedCode:
        expires_at = now + self._invite_ttl_s
        return self._issue_code(session, device_id, BindCodeKind.INVITE, chat_id, expires_at, now)

    @staticmethod
    def _issue_code(
        session: AsyncSession,
        device_id: str,
        kind: BindCodeKind,
        chat_id: int | None,
        expires_at: int | None,
        now: int,
    ) -> IssuedCode:
        code = generate_bind_code()
        session.add(
            BindCode(
                code_hash=hash_bind_code(code),
                device_id=device_id,
                kind=kind,
                created_by_chat_id=chat_id,
                created_at=now,
                expires_at=expires_at,
            )
        )
        return IssuedCode(code=code, expires_at=expires_at)

    async def bind(
        self, session: AsyncSession, chat_id: int, display_name: str, raw_code: str, now: int
    ) -> BindResult:
        guardian = await self._ensure_guardian(session, chat_id, display_name, now)
        if guardian.bind_blocked_until is not None and guardian.bind_blocked_until > now:
            return BindResult(BindOutcome.BLOCKED, blocked_until=guardian.bind_blocked_until)
        code = await repositories.find_bind_code(session, hash_bind_code(raw_code))
        if code is None:
            return self._register_failure(guardian, BindOutcome.UNKNOWN_CODE, now)
        problem = self._code_problem(code, now)
        if problem is not None:
            return self._register_failure(guardian, problem, now)
        return await self._bind_with_code(session, guardian, code, now)

    @staticmethod
    def _code_problem(code: BindCode, now: int) -> BindOutcome | None:
        if code.used_at is not None:
            return BindOutcome.USED_CODE
        if code.expires_at is not None and code.expires_at <= now:
            return BindOutcome.EXPIRED_CODE
        return None

    def _register_failure(self, guardian: Guardian, outcome: BindOutcome, now: int) -> BindResult:
        guardian.bind_failures += 1
        if guardian.bind_failures < self._max_failures:
            return BindResult(outcome)
        guardian.bind_failures = 0
        guardian.bind_blocked_until = now + self._block_s
        return BindResult(BindOutcome.BLOCKED, blocked_until=guardian.bind_blocked_until)

    async def _bind_with_code(
        self, session: AsyncSession, guardian: Guardian, code: BindCode, now: int
    ) -> BindResult:
        device = await repositories.get_device(session, code.device_id)
        if device is None:
            return self._register_failure(guardian, BindOutcome.UNKNOWN_CODE, now)
        guardian.bind_failures = 0
        if await repositories.find_binding(session, device.id, guardian.chat_id) is not None:
            return BindResult(BindOutcome.ALREADY_BOUND, device=device)
        await self._notify_existing_guardians(session, device, guardian, now)
        code.used_at = now
        code.used_by_chat_id = guardian.chat_id
        session.add(DeviceGuardian(device_id=device.id, chat_id=guardian.chat_id, bound_at=now))
        await session.flush()
        count = await repositories.count_guardians(session, device.id)
        return BindResult(BindOutcome.BOUND, device=device, guardians_count=count)

    async def _notify_existing_guardians(
        self, session: AsyncSession, device: Device, guardian: Guardian, now: int
    ) -> None:
        text = self._texts.guardian_joined(device, guardian.display_name)
        await self._notifier.to_guardians(session, device.id, Notification(text=text), now)

    @staticmethod
    async def _ensure_guardian(
        session: AsyncSession, chat_id: int, display_name: str, now: int
    ) -> Guardian:
        guardian = await repositories.get_guardian(session, chat_id)
        if guardian is None:
            guardian = Guardian(
                chat_id=chat_id,
                display_name=display_name,
                created_at=now,
                bind_failures=0,
            )
            session.add(guardian)
            await session.flush()
        else:
            guardian.display_name = display_name
        return guardian

    @staticmethod
    async def unbind(session: AsyncSession, device_id: str, chat_id: int) -> Device | None:
        binding = await repositories.find_binding(session, device_id, chat_id)
        if binding is None:
            return None
        await session.delete(binding)
        return await repositories.get_device(session, device_id)
