from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot_handlers import router
from app.config import get_settings
from app.db import init_db
from app.scheduler import send_daily_gold_matches, setup_scheduler
from app.web import start_web_server


async def main() -> None:
    """Точка входа."""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    settings = get_settings()
    await init_db()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp.include_router(router)

    await start_web_server(bot, settings.web_host, settings.web_port)
    setup_scheduler(bot)

    if settings.run_on_start:
        await send_daily_gold_matches(bot)

    logging.info(
        "TelOnyx Predict Bot запущен. DATA_PROVIDER=%s, public=%s, private=%s",
        settings.provider_normalized,
        settings.telegram_public_channel,
        settings.telegram_private_channel_id,
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
