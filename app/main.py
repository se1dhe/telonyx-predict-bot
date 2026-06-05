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
from app.runtime_lock import wait_for_runtime_lock
from app.scheduler import send_daily_gold_matches, setup_scheduler
from app.services.video_scripts import send_today_video_scripts
from app.web import start_web_server


async def send_pending_video_scripts_on_start(bot: Bot) -> None:
    """Дослать сценарии для уже опубликованных сегодня прогнозов."""
    try:
        sent = await send_today_video_scripts(bot)
        logging.info("Pending video scripts sent on startup: %s", sent)
    except Exception:
        logging.exception("Failed to send pending video scripts on startup")


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

    runtime_lock = await wait_for_runtime_lock()

    try:
        setup_scheduler(bot)
        asyncio.create_task(send_pending_video_scripts_on_start(bot))

        if settings.run_on_start:
            # Важно: не await.
            # Иначе бот не отвечает на /start и кнопки, пока идёт тяжёлый сбор матчей.
            asyncio.create_task(send_daily_gold_matches(bot))

        logging.info(
            "TelOnyx Predict Bot запущен. DATA_PROVIDER=%s, public_languages=%s, private_languages=%s",
            settings.provider_normalized,
            settings.active_public_languages,
            settings.active_private_languages,
        )

        await dp.start_polling(bot)
    finally:
        await runtime_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
