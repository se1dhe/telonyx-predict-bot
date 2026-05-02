from __future__ import annotations
import asyncio, logging, sys
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import get_settings
from app.db import init_db
from app.scheduler import send_daily_gold_matches, setup_scheduler

async def main() -> None:
    """Точка входа."""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    settings = get_settings()
    await init_db()
    bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    setup_scheduler(bot)
    if settings.run_on_start:
        await send_daily_gold_matches(bot)
    logging.info("Football Gold Hybrid Predictor Bot запущен. DATA_PROVIDER=%s", settings.provider_normalized)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
