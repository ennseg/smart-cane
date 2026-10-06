from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import select

from cane_server.bot.dry_run import RecordingSession
from cane_server.context import AppContext
from cane_server.services.notifier import MapPoint, Notification
from cane_server.storage.models import OutboxMessage, OutboxStatus
from tests.conftest import FakeClock

CHAT = 777
OTHER_CHAT = 778
METHOD = SendMessage(chat_id=CHAT, text="x")


async def enqueue(ctx: AppContext, chat_id: int, text: str, point: MapPoint | None = None) -> None:
    async with ctx.db.transaction() as session:
        ctx.notifier.to_chat(
            session, chat_id, Notification(text=text, point=point), ctx.clock.now()
        )


async def outbox_rows(ctx: AppContext) -> list[OutboxMessage]:
    async with ctx.db.transaction() as session:
        return list((await session.scalars(select(OutboxMessage).order_by(OutboxMessage.id))).all())


async def test_message_is_sent_and_marked(ctx: AppContext, telegram: RecordingSession) -> None:
    await enqueue(ctx, CHAT, "привет", MapPoint(59.9, 30.3))
    assert await ctx.outbox.deliver_due() == 2
    assert [call.method for call in telegram.calls] == ["sendMessage", "sendLocation"]
    rows = await outbox_rows(ctx)
    assert all(row.status == OutboxStatus.SENT for row in rows)
    assert rows[0].sent_message_id is not None


async def test_network_error_is_retried_with_backoff(
    ctx: AppContext, telegram: RecordingSession, clock: FakeClock
) -> None:
    await enqueue(ctx, CHAT, "тревога")
    telegram.pending_failures.append(TelegramNetworkError(method=METHOD, message="offline"))
    assert await ctx.outbox.deliver_due() == 0
    row = (await outbox_rows(ctx))[0]
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 1
    assert row.next_attempt_at == clock.now() + 2

    assert await ctx.outbox.deliver_due() == 0
    clock.advance(2)
    assert await ctx.outbox.deliver_due() == 1
    assert telegram.calls[0].text == "тревога"


async def test_retry_after_is_respected(
    ctx: AppContext, telegram: RecordingSession, clock: FakeClock
) -> None:
    await enqueue(ctx, CHAT, "тревога")
    telegram.pending_failures.append(
        TelegramRetryAfter(method=METHOD, message="flood", retry_after=17)
    )
    await ctx.outbox.deliver_due()
    assert (await outbox_rows(ctx))[0].next_attempt_at == clock.now() + 17


async def test_blocked_bot_is_not_retried(ctx: AppContext, telegram: RecordingSession) -> None:
    await enqueue(ctx, CHAT, "тревога")
    telegram.pending_failures.append(TelegramForbiddenError(method=METHOD, message="blocked"))
    await ctx.outbox.deliver_due()
    row = (await outbox_rows(ctx))[0]
    assert row.status == OutboxStatus.FAILED
    assert "blocked" in (row.last_error or "")


async def test_message_fails_after_max_attempts(
    ctx: AppContext, telegram: RecordingSession, clock: FakeClock
) -> None:
    await enqueue(ctx, CHAT, "тревога")
    for _ in range(ctx.settings.outbox_max_attempts):
        telegram.pending_failures.append(TelegramNetworkError(method=METHOD, message="offline"))
        await ctx.outbox.deliver_due()
        clock.advance(600)
    row = (await outbox_rows(ctx))[0]
    assert row.status == OutboxStatus.FAILED
    assert row.attempts == ctx.settings.outbox_max_attempts


async def test_order_within_chat_is_preserved_but_other_chats_proceed(
    ctx: AppContext, telegram: RecordingSession, clock: FakeClock
) -> None:
    await enqueue(ctx, CHAT, "первое")
    await enqueue(ctx, CHAT, "второе")
    await enqueue(ctx, OTHER_CHAT, "другому")
    telegram.pending_failures.append(TelegramNetworkError(method=METHOD, message="offline"))
    await ctx.outbox.deliver_due()
    assert [call.text for call in telegram.calls] == ["другому"]

    clock.advance(2)
    await ctx.outbox.deliver_due()
    assert [call.text for call in telegram.calls] == ["другому", "первое", "второе"]


async def test_live_location_is_sent_with_live_period(
    ctx: AppContext, telegram: RecordingSession
) -> None:
    async with ctx.db.transaction() as session:
        ctx.notifier.to_chat(
            session,
            CHAT,
            Notification(text="SOS", point=MapPoint(59.9, 30.3), live_period_s=600),
            ctx.clock.now(),
        )
    await ctx.outbox.deliver_due()
    assert telegram.calls[1].method == "sendLocation"
    assert telegram.calls[1].params["live_period"] == 600
