from __future__ import annotations

import asyncio
import logging
import traceback

from aiogram import Bot
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.i18n import normalize_lang
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
    if not str(chat_id or "").strip():
        return

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
                "⚠️ Bot collected data, but could not send a Telegram message.\n"
                "Details are written to Railway Logs."
            ),
            parse_mode=None,
            disable_web_page_preview=True,
        )


def _get_lang_text(data: dict[str, str], lang: str) -> str:
    lang = normalize_lang(lang)
    return data.get(lang) or data.get("uk") or next(iter(data.values()))


def _get_lang_details(data: dict[str, list[str]], lang: str) -> list[str]:
    lang = normalize_lang(lang)
    return data.get(lang) or data.get("uk") or next(iter(data.values()), [])


async def _notify_admin_channels(bot: Bot, text: str) -> None:
    """Отправить техническую ошибку хотя бы в заполненные приватные каналы."""
    settings = get_settings()
    sent = set()
    for lang in settings.active_private_languages:
        chat_id = settings.private_channel_for(lang)
        if chat_id and chat_id not in sent:
            sent.add(chat_id)
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=None, disable_web_page_preview=True)


async def send_daily_gold_matches(bot: Bot) -> None:
    """Ежедневный запуск прогнозов.

    Один анализ матчей рендерится в разные языковые каналы.
    Если канал для языка не заполнен в .env — этот язык пропускается.
    """
    settings = get_settings()

    if pipeline_lock.locked():
        logger.warning("Daily predictions are already running, duplicate launch skipped")
        return

    try:
        async with pipeline_lock:
            logger.info("Запускаю ежедневный сбор прогнозов")
            summaries, details_by_lang = await asyncio.wait_for(
                DailyPipeline().run_for_today(force=True),
                timeout=settings.pipeline_timeout_seconds,
            )

            total_details = max((len(v) for v in details_by_lang.values()), default=0)
            logger.info("Сводка собрана. Детальных прогнозов: %s", total_details)

            # Приватные каналы: полная сводка + все карточки.
            for lang in settings.active_private_languages:
                private_chat = settings.private_channel_for(lang)
                summary = _get_lang_text(summaries, lang)
                details = _get_lang_details(details_by_lang, lang)

                await safe_send_html(bot, private_chat, private_summary(summary, lang=lang))

                if settings.show_detailed_picks:
                    for detail in details:
                        await safe_send_html(
                            bot,
                            private_chat,
                            detail[:3850] + "\n\n..." if len(detail) > 3900 else detail,
                        )

            # Публичные каналы: только самый сильный матч дня + зелёная CTA-кнопка.
            for lang in settings.active_public_languages:
                public_chat = settings.public_channel_for(lang)
                summary = _get_lang_text(summaries, lang)
                details = _get_lang_details(details_by_lang, lang)
                public_reply_markup = public_channel_cta_keyboard(lang)

                if details:
                    await safe_send_html(
                        bot,
                        public_chat,
                        public_summary_from_private(summary, details[0][:3400], lang=lang),
                        reply_markup=public_reply_markup,
                    )
                else:
                    await safe_send_html(
                        bot,
                        public_chat,
                        public_summary_from_private(summary, None, lang=lang),
                        reply_markup=public_reply_markup,
                    )

    except asyncio.TimeoutError:
        logger.exception("Pipeline завис дольше разрешённого времени")
        await _notify_admin_channels(
            bot,
            "⚠️ Prediction collection stopped by timeout.\n\nDetails are written to Railway Logs.",
        )

    except Exception:
        logger.exception("Ошибка при сборе прогнозов")
        logger.error("Полный traceback:\n%s", traceback.format_exc())
        await _notify_admin_channels(
            bot,
            "⚠️ Error while collecting predictions.\n\nDetails are written to Railway Logs.",
        )


async def check_results(bot: Bot) -> None:
    """Проверить результаты открытых прогнозов."""
    try:
        logger.info("Запускаю проверку результатов")
        await ResultChecker(bot).check_open_predictions()

    except Exception:
        logger.exception("Ошибка при проверке результатов")
        logger.error("Полный traceback:\n%s", traceback.format_exc())
        await _notify_admin_channels(
            bot,
            "⚠️ Error while checking results.\n\nDetails are written to Railway Logs.",
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
        await _notify_admin_channels(
            bot,
            "⚠️ Error while sending statistics.\n\nDetails are written to Railway Logs.",
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
