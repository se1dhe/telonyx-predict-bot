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


logger = logging.getLogger(__name__)


async def safe_send_html(
    bot: Bot,
    chat_id: str,
    text: str,
    disable_web_page_preview: bool = True,
) -> None:
    """Безопасно отправить HTML в Telegram."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=disable_web_page_preview,
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
    """Ежедневный запуск прогнозов."""
    settings = get_settings()

    try:
        logger.info("Запускаю ежедневный сбор прогнозов")
        summary, details = await asyncio.wait_for(
            DailyPipeline().run_for_today(force=True),
            timeout=settings.pipeline_timeout_seconds,
        )

        logger.info("Сводка собрана. Детальных прогнозов: %s", len(details))
        await safe_send_html(bot, settings.telegram_target_chat_id, summary)

        if settings.show_detailed_picks:
            for detail in details:
                await safe_send_html(
                    bot,
                    settings.telegram_target_chat_id,
                    detail[:3850] + "\n\n..." if len(detail) > 3900 else detail,
                )

    except asyncio.TimeoutError:
        logger.exception("Pipeline завис дольше разрешённого времени")
        await bot.send_message(
            chat_id=settings.telegram_target_chat_id,
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
            chat_id=settings.telegram_target_chat_id,
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
            chat_id=settings.telegram_target_chat_id,
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
            chat_id=settings.telegram_target_chat_id,
            text="⚠️ Ошибка при отправке статистики.\n\nПодробности записаны в Railway Logs.",
            parse_mode=None,
            disable_web_page_preview=True,
        )


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

    scheduler.start()
    return scheduler
