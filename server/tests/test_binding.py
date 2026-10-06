from cane_server.bot.simulated_chat import SimulatedChat
from cane_server.context import AppContext
from cane_server.services.binding import generate_bind_code, normalize_bind_code
from cane_server.storage import repositories
from tests.conftest import GUARDIAN_A, GUARDIAN_B, STRANGER, DeviceClient, FakeClock, issue_invite


async def bound_chats(ctx: AppContext) -> list[int]:
    async with ctx.db.transaction() as session:
        return await repositories.guardian_chat_ids(session, "cane-01")


def test_generated_code_format() -> None:
    code = generate_bind_code()
    assert len(code) == 9
    assert code[4] == "-"
    assert not set(code.replace("-", "")) & set("01OI")


def test_code_normalization() -> None:
    assert normalize_bind_code(" abcd-efgh ") == "ABCDEFGH"
    assert normalize_bind_code("AbCd EfGh") == "ABCDEFGH"


async def test_correct_code_binds(ctx: AppContext, chat: SimulatedChat, cane: DeviceClient) -> None:
    replies = await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}")
    assert "Вы привязаны к трости «Трость Ивана»" in replies[0].text
    assert await bound_chats(ctx) == [GUARDIAN_A]


async def test_code_is_case_and_dash_insensitive(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient
) -> None:
    sloppy = cane.bind_code.lower().replace("-", " ")
    replies = await chat.send_text(GUARDIAN_A, f"/bind {sloppy}")
    assert "Вы привязаны" in replies[0].text


async def test_wrong_code_is_refused(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient
) -> None:
    replies = await chat.send_text(GUARDIAN_A, "/bind ZZZZ-ZZZZ")
    assert "Код не найден" in replies[0].text
    assert await bound_chats(ctx) == []


async def test_bind_without_code_shows_usage(chat: SimulatedChat) -> None:
    replies = await chat.send_text(GUARDIAN_A, "/bind")
    assert "Укажите код" in replies[0].text


async def test_used_code_cannot_be_reused(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient
) -> None:
    await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}")
    replies = await chat.send_text(GUARDIAN_B, f"/bind {cane.bind_code}")
    assert "уже использован" in replies[0].text
    assert await bound_chats(ctx) == [GUARDIAN_A]


async def test_rebinding_same_chat_reports_already_bound(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient
) -> None:
    await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}")
    code = await issue_invite(ctx, "cane-01", GUARDIAN_A)
    replies = await chat.send_text(GUARDIAN_A, f"/bind {code}")
    assert "уже привязаны" in replies[0].text
    replies = await chat.send_text(GUARDIAN_B, f"/bind {code}")
    assert "Вы привязаны" in replies[0].text


async def test_expired_invite_is_refused(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient, clock: FakeClock
) -> None:
    await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}")
    code = await issue_invite(ctx, "cane-01", GUARDIAN_A)
    clock.advance(ctx.settings.invite_ttl_h * 3600 + 1)
    replies = await chat.send_text(GUARDIAN_B, f"/bind {code}")
    assert "Срок действия кода истёк" in replies[0].text


async def test_guessing_is_blocked_after_max_failures(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient, clock: FakeClock
) -> None:
    for attempt in range(ctx.settings.bind_max_failures - 1):
        replies = await chat.send_text(STRANGER, f"/bind AAAA-AAA{attempt + 2}")
        assert "Код не найден" in replies[0].text
    replies = await chat.send_text(STRANGER, "/bind AAAA-AAAA")
    assert "Слишком много неверных попыток" in replies[0].text

    replies = await chat.send_text(STRANGER, f"/bind {cane.bind_code}")
    assert "Слишком много неверных попыток" in replies[0].text
    assert await bound_chats(ctx) == []

    clock.advance(ctx.settings.bind_block_min * 60 + 1)
    replies = await chat.send_text(STRANGER, f"/bind {cane.bind_code}")
    assert "Вы привязаны" in replies[0].text


async def test_success_resets_failure_counter(
    ctx: AppContext, chat: SimulatedChat, cane: DeviceClient
) -> None:
    for _ in range(ctx.settings.bind_max_failures - 1):
        await chat.send_text(GUARDIAN_A, "/bind BAD0-CODE")
    await chat.send_text(GUARDIAN_A, f"/bind {cane.bind_code}")
    async with ctx.db.transaction() as session:
        guardian = await repositories.get_guardian(session, GUARDIAN_A)
    assert guardian is not None
    assert guardian.bind_failures == 0


async def test_several_guardians_per_device(
    ctx: AppContext, chat: SimulatedChat, bound_cane: DeviceClient
) -> None:
    assert await bound_chats(ctx) == [GUARDIAN_A, GUARDIAN_B]
