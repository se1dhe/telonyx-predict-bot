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

from app.config import get_settings
from app.db import init_db
from app.services.video_scripts import send_video_scripts_for_date


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send short-video scripts for saved predictions.")
    parser.add_argument("dates", nargs="*", help="ISO dates, for example 2026-06-05")
    parser.add_argument("--today", action="store_true", help="Use today's date in configured timezone")
    parser.add_argument("--tomorrow", action="store_true", help="Use tomorrow's date in configured timezone")
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
            sent = await send_video_scripts_for_date(bot, target_date)
            logger.info("Video scripts sent date=%s count=%s", target_date.isoformat(), sent)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
