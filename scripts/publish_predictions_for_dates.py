from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import DailyRun, Prediction
from app.pipeline import DailyPipeline
from app.scheduler import _get_lang_details, _get_lang_text, safe_send_html
from app.services.channel_buttons import public_channel_cta_keyboard
from app.services.channel_render import private_summary, public_summary_from_private
from app.services.post_refs import PostRef, dumps_refs


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish predictions for explicit dates.")
    parser.add_argument("dates", nargs="*", help="ISO dates, for example 2026-05-27")
    parser.add_argument("--today", action="store_true", help="Use today's date in configured timezone")
    parser.add_argument("--tomorrow", action="store_true", help="Use tomorrow's date in configured timezone")
    parser.add_argument("--clear", action="store_true", help="Delete existing daily run and predictions first")
    parser.add_argument("--no-send", action="store_true", help="Build and save, but do not send Telegram messages")
    return parser.parse_args()


def requested_dates(args: argparse.Namespace) -> list[date]:
    settings = get_settings()
    today = datetime.now(ZoneInfo(settings.tz)).date()
    values = [date.fromisoformat(value) for value in args.dates]
    if args.today:
        values.append(today)
    if args.tomorrow:
        values.append(today + timedelta(days=1))
    if not values:
        values.append(today)

    result: list[date] = []
    seen: set[str] = set()
    for value in values:
        key = value.isoformat()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


async def clear_date(date_key: str, provider: str) -> None:
    async with SessionLocal() as session:
        pred_result = await session.execute(
            delete(Prediction)
            .where(Prediction.date_key == date_key)
            .where(Prediction.provider == provider)
        )
        run_result = await session.execute(delete(DailyRun).where(DailyRun.date_key == date_key))
        await session.commit()

    logger.info(
        "Cleared date=%s provider=%s predictions=%s daily_runs=%s",
        date_key,
        provider,
        pred_result.rowcount,
        run_result.rowcount,
    )


async def save_message_refs(
    date_key: str,
    provider: str,
    private_refs_by_index: dict[int, list[PostRef]],
    public_refs_by_index: dict[int, list[PostRef]],
    expected_count: int,
) -> None:
    if not private_refs_by_index and not public_refs_by_index:
        return

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Prediction)
            .where(Prediction.date_key == date_key)
            .where(Prediction.provider == provider)
            .where(Prediction.rendered_text != "")
            .order_by(Prediction.start_time.asc(), Prediction.ai_rank_score.desc())
        )).scalars().all()

        for index, prediction in enumerate(rows[:expected_count]):
            prediction.private_message_refs = dumps_refs(private_refs_by_index.get(index, []))
            prediction.public_message_refs = dumps_refs(public_refs_by_index.get(index, []))

        await session.commit()
        logger.info("Saved refs date=%s predictions=%s expected=%s", date_key, len(rows[:expected_count]), expected_count)


async def publish_date(bot: Bot, target_date: date, clear: bool, send: bool) -> None:
    settings = get_settings()
    provider = "API_FOOTBALL" if settings.odds_first_enabled else settings.provider_normalized
    date_key = target_date.isoformat()

    if clear:
        await clear_date(date_key, provider)

    summaries, details_by_lang = await DailyPipeline().run_for_date(target_date, force=True)
    total_details = max((len(v) for v in details_by_lang.values()), default=0)
    logger.info("Built date=%s details=%s send=%s", date_key, total_details, send)

    if not send:
        return

    private_refs_by_index: dict[int, list[PostRef]] = {}
    public_refs_by_index: dict[int, list[PostRef]] = {}

    for lang in settings.active_private_languages:
        private_chat = settings.private_channel_for(lang)
        summary = _get_lang_text(summaries, lang)
        details = _get_lang_details(details_by_lang, lang)

        await safe_send_html(bot, private_chat, private_summary(summary, lang=lang))

        if settings.show_detailed_picks:
            for index, detail in enumerate(details):
                msg = await safe_send_html(
                    bot,
                    private_chat,
                    detail[:3850] + "\n\n..." if len(detail) > 3900 else detail,
                )
                if msg:
                    private_refs_by_index.setdefault(index, []).append(
                        PostRef(lang=lang, chat_id=str(private_chat), message_id=msg.message_id, kind="private")
                    )

    for lang in settings.active_public_languages:
        public_chat = settings.public_channel_for(lang)
        summary = _get_lang_text(summaries, lang)
        details = _get_lang_details(details_by_lang, lang)

        msg = await safe_send_html(
            bot,
            public_chat,
            public_summary_from_private(summary, details[0][:3400] if details else None, lang=lang),
            reply_markup=public_channel_cta_keyboard(lang),
        )
        if msg and details:
            public_refs_by_index.setdefault(0, []).append(
                PostRef(lang=lang, chat_id=str(public_chat), message_id=msg.message_id, kind="public")
            )

    await save_message_refs(date_key, provider, private_refs_by_index, public_refs_by_index, total_details)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    args = parse_args()
    settings = get_settings()

    await init_db()
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        for target_date in requested_dates(args):
            await publish_date(bot, target_date, clear=args.clear, send=not args.no_send)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
