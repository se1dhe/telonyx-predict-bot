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
from app.runtime_lock import acquire_runtime_lock, idle_without_telegram_runtime
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

    runtime_lock = await acquire_runtime_lock()
    if not runtime_lock.acquired:
        logging.warning(
            "TelOnyx Predict Bot запущен как web-only replica. "
            "Telegram polling и scheduler отключены, потому что активен другой инстанс."
        )
        await idle_without_telegram_runtime()
        return

    try:
        setup_scheduler(bot)

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
