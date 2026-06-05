from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.i18n import normalize_lang
from app.models import Prediction
from app.pipeline import DailyPipeline
from app.result_checker import ResultChecker
from app.services.bookmaker_post_updater import BookmakerPostUpdater
from app.services.channel_render import private_summary, public_summary_from_private
from app.services.channel_buttons import public_channel_cta_keyboard
from app.services.post_refs import PostRef, dumps_refs
from app.services.subscription_guard import check_subscriptions
from app.services.video_scripts import send_today_video_scripts


logger = logging.getLogger(__name__)

pipeline_lock = asyncio.Lock()


async def safe_send_html(
    bot: Bot,
    chat_id: str,
    text: str,
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
) -> Message | None:
    """Безопасно отправить HTML в Telegram.

    Возвращаем Message, чтобы сохранить message_id и позже отредактировать
    прогноз букмекерской ссылкой за 10 минут до старта.
    """
    if not str(chat_id or "").strip():
        logger.info("Telegram send skipped: empty chat_id")
        return None

    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.exception("Не удалось отправить HTML-сообщение в Telegram chat_id=%s", chat_id)
        logger.error("Telegram send error: %s", exc)
        logger.error("Проблемный текст сообщения:\n%s", text)
        return None


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
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=None, disable_web_page_preview=True)
            except Exception as exc:
                logger.exception("Не удалось отправить техническое уведомление в chat_id=%s: %s", chat_id, exc)


async def send_daily_gold_matches(bot: Bot) -> None:
    """Ежедневный запуск прогнозов."""
    settings = get_settings()

    if pipeline_lock.locked():
        logger.warning("Daily predictions are already running, duplicate launch skipped")
        return

    try:
        async with pipeline_lock:
            if await already_published_today():
                logger.info("Daily predictions already published today; startup/retry launch skipped")
                sent_video_scripts = await send_today_video_scripts(bot)
                logger.info("Pending video scripts sent to owner after already-published check: %s", sent_video_scripts)
                return

            logger.info("Запускаю ежедневный сбор прогнозов")
            summaries, details_by_lang = await asyncio.wait_for(
                DailyPipeline().run_for_today(force=False),
                timeout=settings.pipeline_timeout_seconds,
            )

            total_details = max((len(v) for v in details_by_lang.values()), default=0)
            logger.info("Сводка собрана. Детальных прогнозов: %s", total_details)

            private_refs_by_index: dict[int, list[PostRef]] = {}
            public_refs_by_index: dict[int, list[PostRef]] = {}

            # Приватные каналы: полная сводка + все карточки.
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

            # Публичные каналы: только самый сильный матч дня + зелёная CTA-кнопка.
            for lang in settings.active_public_languages:
                public_chat = settings.public_channel_for(lang)
                summary = _get_lang_text(summaries, lang)
                details = _get_lang_details(details_by_lang, lang)
                public_reply_markup = public_channel_cta_keyboard(lang)

                if details:
                    msg = await safe_send_html(
                        bot,
                        public_chat,
                        public_summary_from_private(summary, details[0][:3400], lang=lang),
                        reply_markup=public_reply_markup,
                    )
                    if msg:
                        public_refs_by_index.setdefault(0, []).append(
                            PostRef(lang=lang, chat_id=str(public_chat), message_id=msg.message_id, kind="public")
                        )
                else:
                    await safe_send_html(
                        bot,
                        public_chat,
                        public_summary_from_private(summary, None, lang=lang),
                        reply_markup=public_reply_markup,
                    )

            await save_sent_message_refs(
                private_refs_by_index,
                public_refs_by_index,
                expected_count=total_details,
            )

            sent_video_scripts = await send_today_video_scripts(bot, limit=total_details)
            logger.info("Video scripts sent to owner: %s", sent_video_scripts)

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


async def already_published_today() -> bool:
    """Не публиковать повторный daily-run после redeploy, если карточки уже ушли."""
    settings = get_settings()
    provider = "API_FOOTBALL" if (settings.odds_first_enabled or settings.ggbet_odds_first_enabled) else settings.provider_normalized
    date_key = datetime.now(ZoneInfo(settings.tz)).date().isoformat()

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Prediction)
            .where(Prediction.provider == provider)
            .where(Prediction.date_key == date_key)
            .where(Prediction.rendered_text != "")
        )).scalars().all()

    for row in rows:
        if str(row.private_message_refs or "").strip() or str(row.public_message_refs or "").strip():
            return True
    return False


async def save_sent_message_refs(
    private_refs_by_index: dict[int, list[PostRef]],
    public_refs_by_index: dict[int, list[PostRef]],
    expected_count: int,
) -> None:
    """Сохранить message_id только для прогнозов текущего запуска.

    Раньше функция брала все predictions последней даты и могла сохранить refs
    для старых строк, если за день был повторный RUN_ON_START/redeploy. Теперь
    берём только сегодняшние опубликованные карточки и обрезаем список по
    количеству реально отправленных деталей.
    """
    if not private_refs_by_index and not public_refs_by_index:
        return

    settings = get_settings()
    provider = "API_FOOTBALL" if (settings.odds_first_enabled or settings.ggbet_odds_first_enabled) else settings.provider_normalized
    date_key = datetime.now(ZoneInfo(settings.tz)).date().isoformat()

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Prediction)
            .where(Prediction.provider == provider)
            .where(Prediction.date_key == date_key)
            .where(Prediction.rendered_text != "")
            .order_by(Prediction.start_time.asc(), Prediction.ai_rank_score.desc())
        )).scalars().all()

        rows = rows[:expected_count]

        for index, prediction in enumerate(rows):
            prediction.private_message_refs = dumps_refs(private_refs_by_index.get(index, []))
            prediction.public_message_refs = dumps_refs(public_refs_by_index.get(index, []))

        await session.commit()
        logger.info(
            "Saved Telegram message refs for predictions=%s date=%s expected=%s",
            len(rows),
            date_key,
            expected_count,
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


async def refresh_bookmaker_links(bot: Bot) -> None:
    """За 10 минут до старта пробуем добавить точную ссылку букмекера в уже опубликованный пост."""
    try:
        updated = await BookmakerPostUpdater(bot).update_due_predictions()
        if updated:
            logger.info("Bookmaker links refreshed and Telegram posts edited: %s", updated)
    except Exception:
        logger.exception("Ошибка при позднем обновлении букмекерских ссылок")


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
        minutes=settings.result_check_interval_minutes,
        args=[bot],
        id="check_results",
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_bookmaker_links,
        trigger="interval",
        minutes=int(getattr(settings, "bookmaker_late_refresh_interval_minutes", 5) or 5),
        args=[bot],
        id="refresh_bookmaker_links",
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
