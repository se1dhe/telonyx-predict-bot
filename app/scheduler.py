from __future__ import annotations

import asyncio
import logging
import traceback

from aiogram import Bot
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.pipeline import DailyPipeline
from app.result_checker import ResultChecker
from app.services.channel_render import private_summary, public_summary_from_private
from app.services.channel_buttons import public_channel_cta_keyboard
from app.services.subscription_guard import check_subscriptions


logger = logging.getLogger(__name__)

pipeline_lock = asyncio.Lock()


async def safe_send_html(
    bot: Bot,
    chat_id: str,
    text: str,
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
) -> None:
    """Безопасно отправить HTML в Telegram."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("Не удалось отправить HTML-сообщение в Telegram")
        logger.error("Проблемный текст сообщения:\n%s", text)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Бот собрал данные, но не смог отправить сообщение в Telegram.\n"
                "Подробная ошибка записана в Railway Logs."
            ),
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def send_daily_gold_matches(bot: Bot) -> None:
    """Ежедневный запуск прогнозов.

    Задача может идти долго, поэтому:
    - она запускается в фоне из main.py;
    - polling стартует сразу;
    - lock не даёт запустить два сбора одновременно.
    """
    settings = get_settings()

    if pipeline_lock.locked():
        logger.warning("Сбор прогнозов уже выполняется, повторный запуск пропущен")
        return

    try:
        async with pipeline_lock:
            logger.info("Запускаю ежедневный сбор прогнозов")
            summary, details = await asyncio.wait_for(
                DailyPipeline().run_for_today(force=True),
                timeout=settings.pipeline_timeout_seconds,
            )

            logger.info("Сводка собрана. Детальных прогнозов: %s", len(details))

            private_chat = settings.telegram_private_channel_id
            public_chat = settings.telegram_public_channel

            # Приватный канал: полная сводка + все карточки.
            await safe_send_html(bot, private_chat, private_summary(summary))

            if settings.show_detailed_picks:
                for detail in details:
                    await safe_send_html(
                        bot,
                        private_chat,
                        detail[:3850] + "\n\n..." if len(detail) > 3900 else detail,
                    )

            # Открытый канал: только самый сильный матч дня.
            public_reply_markup = public_channel_cta_keyboard()

            if details:
                await safe_send_html(
                    bot,
                    public_chat,
                    public_summary_from_private(summary, details[0][:3400]),
                    reply_markup=public_reply_markup,
                )
            else:
                await safe_send_html(
                    bot,
                    public_chat,
                    public_summary_from_private(summary, None),
                    reply_markup=public_reply_markup,
                )

    except asyncio.TimeoutError:
        logger.exception("Pipeline завис дольше разрешённого времени")
        await bot.send_message(
            chat_id=settings.telegram_private_channel_id,
            text=(
                "⚠️ Сбор прогнозов остановлен по таймауту.\n\n"
                "Бот не упал, но внешний источник или AI отвечал слишком долго. "
                "Подробности записаны в Railway Logs."
            ),
            parse_mode=None,
            disable_web_page_preview=True,
        )

    except Exception:
        logger.exception("Ошибка при сборе прогнозов")
        logger.error("Полный traceback:\n%s", traceback.format_exc())
        await bot.send_message(
            chat_id=settings.telegram_private_channel_id,
            text="⚠️ Ошибка при сборе прогнозов.\n\nПодробности записаны в Railway Logs.",
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def check_results(bot: Bot) -> None:
    """Проверить результаты открытых прогнозов."""
    settings = get_settings()

    try:
        logger.info("Запускаю проверку результатов")
        await ResultChecker(bot).check_open_predictions()

    except Exception:
        logger.exception("Ошибка при проверке результатов")
        logger.error("Полный traceback:\n%s", traceback.format_exc())
        await bot.send_message(
            chat_id=settings.telegram_private_channel_id,
            text="⚠️ Ошибка при проверке результатов.\n\nПодробности записаны в Railway Logs.",
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def send_daily_stats_report(bot: Bot) -> None:
    """Отправить отчёт winrate в конце игрового дня."""
    settings = get_settings()

    if not settings.stats_report_enabled:
        logger.info("Ежедневная статистика отключена")
        return

    try:
        logger.info("Запускаю ежедневный отчёт статистики")
        sent = await ResultChecker(bot).send_daily_stats_report(force=False)
        logger.info("Ежедневный отчёт статистики отправлен: %s", sent)

    except Exception:
        logger.exception("Ошибка при отправке статистики")
        logger.error("Полный traceback:\n%s", traceback.format_exc())
        await bot.send_message(
            chat_id=settings.telegram_private_channel_id,
            text="⚠️ Ошибка при отправке статистики.\n\nПодробности записаны в Railway Logs.",
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def check_subscription_access(bot: Bot) -> None:
    """Проверить подписки, уведомить и удалить истёкших пользователей."""
    try:
        await check_subscriptions(bot)
    except Exception:
        logger.exception("Ошибка при проверке подписок")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Настроить расписание."""
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    scheduler.add_job(
        send_daily_gold_matches,
        trigger="cron",
        hour=settings.daily_run_hour,
        minute=0,
        args=[bot],
        id="daily_gold_matches",
        replace_existing=True,
    )

    scheduler.add_job(
        check_results,
        trigger="interval",
        hours=1,
        args=[bot],
        id="check_results",
        replace_existing=True,
    )

    scheduler.add_job(
        send_daily_stats_report,
        trigger="cron",
        hour=settings.daily_stats_hour,
        minute=settings.daily_stats_minute,
        args=[bot],
        id="daily_stats_report",
        replace_existing=True,
    )

    scheduler.add_job(
        check_subscription_access,
        trigger="interval",
        minutes=settings.subscription_check_interval_minutes,
        args=[bot],
        id="subscription_guard",
        replace_existing=True,
    )

    scheduler.start()
    return scheduler
