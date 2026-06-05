from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Prediction
from app.schemas import AiPick
from app.services.render import bet_name, html_escape, simple_bet_name
from app.services.video_assets import generate_video_asset_for_prediction


logger = logging.getLogger(__name__)


def provider_name_for_settings() -> str:
    settings = get_settings()
    if settings.odds_first_enabled or settings.ggbet_odds_first_enabled:
        return "API_FOOTBALL"
    return settings.provider_normalized


async def load_predictions_for_video_scripts(
    date_key: str,
    provider: str,
    limit: int | None = None,
    only_pending: bool = True,
) -> list[Prediction]:
    settings = get_settings()
    async with SessionLocal() as session:
        stmt = (
            select(Prediction)
            .where(Prediction.date_key == date_key)
            .where(Prediction.provider == provider)
            .where(Prediction.rendered_text != "")
            .order_by(Prediction.start_time.asc(), Prediction.ai_rank_score.desc())
        )
        if only_pending:
            if settings.video_assets_enabled and not settings.video_assets_send_text_script:
                stmt = stmt.where(Prediction.video_asset_sent_at.is_(None))
            elif settings.video_assets_enabled:
                stmt = stmt.where(
                    Prediction.video_script_sent_at.is_(None)
                    | Prediction.video_asset_sent_at.is_(None)
                )
            else:
                stmt = stmt.where(Prediction.video_script_sent_at.is_(None))

        rows = (await session.execute(stmt)).scalars().all()

    return rows[:limit] if limit is not None else rows


async def send_today_video_scripts(bot: Bot, limit: int | None = None, only_pending: bool = True) -> int:
    settings = get_settings()
    date_key = datetime.now(ZoneInfo(settings.tz)).date().isoformat()
    return await send_video_scripts_for_date(bot, date.fromisoformat(date_key), limit=limit, only_pending=only_pending)


async def send_video_scripts_for_date(
    bot: Bot,
    target_date: date,
    limit: int | None = None,
    only_pending: bool = True,
    force_video_assets: bool = False,
) -> int:
    settings = get_settings()
    if not settings.video_scripts_enabled:
        logger.info("Video script notifications are disabled")
        return 0

    recipient = str(settings.video_scripts_user_id or "").strip()
    if not recipient:
        logger.info("Video script recipient is empty")
        return 0

    provider = provider_name_for_settings()
    predictions = await load_predictions_for_video_scripts(
        target_date.isoformat(),
        provider,
        limit=limit,
        only_pending=only_pending,
    )
    return await send_video_scripts(bot, recipient, predictions, force_video_assets=force_video_assets)


async def send_video_scripts(
    bot: Bot,
    recipient_chat_id: str,
    predictions: list[Prediction],
    force_video_assets: bool = False,
) -> int:
    sent = 0
    for index, prediction in enumerate(predictions, start=1):
        text_sent = False
        asset_sent = False
        text = render_video_script_message(prediction, index=index, total=len(predictions))
        try:
            settings = get_settings()
            if settings.video_assets_send_text_script and prediction.video_script_sent_at is None:
                await bot.send_message(
                    chat_id=recipient_chat_id,
                    text=text[:4000],
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                await mark_video_script_sent(prediction.id)
                text_sent = True

            if settings.video_assets_enabled and (force_video_assets or prediction.video_asset_sent_at is None):
                asset = await generate_video_asset_for_prediction(prediction)
                if asset:
                    await bot.send_video(
                        chat_id=recipient_chat_id,
                        video=FSInputFile(asset.path),
                        caption=asset.caption[:1024],
                        parse_mode=None,
                        supports_streaming=True,
                    )
                    await mark_video_asset_sent(prediction.id)
                    asset_sent = True
                    cleanup_video_asset(asset.path)

            if text_sent or asset_sent:
                sent += 1
        except Exception as exc:
            logger.exception("Failed to send video script prediction=%s to user=%s: %s", prediction.id, recipient_chat_id, exc)
    return sent


async def mark_video_script_sent(prediction_id: int) -> None:
    async with SessionLocal() as session:
        prediction = await session.get(Prediction, prediction_id)
        if prediction:
            prediction.video_script_sent_at = datetime.utcnow()
            await session.commit()


async def mark_video_asset_sent(prediction_id: int) -> None:
    async with SessionLocal() as session:
        prediction = await session.get(Prediction, prediction_id)
        if prediction:
            prediction.video_asset_sent_at = datetime.utcnow()
            await session.commit()


def cleanup_video_asset(path: Path) -> None:
    settings = get_settings()
    if settings.video_assets_keep_files:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to remove generated video asset: %s", path)


def render_video_script_message(prediction: Prediction, index: int = 1, total: int = 1) -> str:
    pick = parse_pick(prediction)
    match_title = f"{prediction.home_team} — {prediction.away_team}".strip(" —")
    if pick and pick.match_title:
        match_title = pick.match_title

    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    risky_bet = simple_bet_name(pick.risky_bet_label, "ru") if pick else "рискованный вариант из полного разбора"
    confidence = pick.confidence if pick else prediction.confidence
    reasoning = compact_text((pick.reasoning if pick else "") or prediction.rendered_text, 430)
    why = compact_text((pick.why_this_match_is_gold if pick else "") or "Матч прошел фильтр по форме, голевым трендам и качеству данных.", 280)
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or get_settings().bookmaker_name
    odds = prediction.bookmaker_odds or (f"{pick.bookmaker_odds:.2f}" if pick and pick.bookmaker_odds else "")
    odds_text = f" с кэфом {odds}" if odds else ""
    league = " • ".join(x for x in [prediction.country, prediction.league_name] if x)
    time_text = format_start_time(prediction.start_time)

    return "\n".join(
        [
            f"🎬 <b>Shorts/TikTok сценарий {index}/{total}</b>",
            f"⚽️ <b>{html_escape(match_title)}</b>",
            "",
            f"🎯 <b>Главная идея:</b> нейросеть против букмекеров: быстро показать, почему ставка {html_escape(main_bet)} выглядит логичной, а полный разбор увести в Telegram.",
            "",
            "<b>Формат вертикального видео:</b>",
            "• 9:16, 30-45 секунд, без лица.",
            "• Фон: темный технологичный шаблон, графики, названия команд, счетчик уверенности.",
            "• Если есть нарезка моментов команд, ставь ее под текст; если нет — строгий AI/football фон.",
            "",
            "<b>Сценарий по кадрам:</b>",
            f"1. 0-3 сек: крупно матч {html_escape(match_title)} и хук «Нейросеть нашла ставку против линии букмекера».",
            f"2. 3-10 сек: показать источник данных: API-Football + {html_escape(bookmaker)}; вывести уверенность {confidence}/100.",
            f"3. 10-25 сек: вывести основную ставку: {html_escape(main_bet)}. Подложить 2-3 цифры/аргумента из разбора.",
            f"4. 25-35 сек: подчеркнуть риск: это не гарантия, а статистический фильтр. Рискованный вариант не раскрывать.",
            f"5. 35-45 сек: CTA: «Полный разбор, рискованный вариант{html_escape(odds_text)} и ссылка на линию уже в Telegram».",
            "",
            "<b>Текст для озвучки:</b>",
            f"«Нейросеть проанализировала матч {html_escape(match_title)} через API-Football и линию {html_escape(bookmaker)}. Уверенность модели — {confidence} из 100. Основная ставка — {html_escape(main_bet)}. Почему? {html_escape(reasoning)} Поэтому я не играю исход вслепую, а беру рынок, который статистически выглядит чище. Рискованный вариант{html_escape(odds_text)} и полный разбор уже загрузил в Telegram. Ссылка в профиле».",
            "",
            "<b>Текст на экране:</b>",
            f"• {html_escape(match_title)}",
            f"• AI confidence: {confidence}/100",
            f"• Main pick: {html_escape(main_bet)}",
            f"• Why: {html_escape(why)}",
            "• Full breakdown in Telegram",
            "",
            f"<b>Справка:</b> {html_escape(league or 'турнир не указан')} | {html_escape(time_text)} | risky: {html_escape(risky_bet)}",
        ]
    )


def parse_pick(prediction: Prediction) -> AiPick | None:
    try:
        if not prediction.prediction_json:
            return None
        return AiPick.model_validate_json(prediction.prediction_json)
    except Exception:
        logger.exception("Failed to parse prediction_json for prediction=%s", prediction.id)
        return None


def compact_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_start_time(value: str) -> str:
    if not value:
        return "время не указано"
    settings = get_settings()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(settings.tz)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value
