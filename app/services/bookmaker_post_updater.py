from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.i18n import normalize_lang
from app.models import Prediction
from app.schemas import AiPick
from app.services.bookmaker_resolver import BookmakerResolver
from app.services.channel_render import public_summary_from_private
from app.services.post_refs import loads_refs
from app.services.render import render_pick_detail

logger = logging.getLogger(__name__)


class BookmakerPostUpdater:
    """Позднее обновление опубликованных прогнозов букмекерской ссылкой.

    Утром точная страница матча может ещё не индексироваться и не открываться.
    Поэтому за несколько минут до старта пробуем найти event URL и редактируем
    уже опубликованные посты в Telegram.
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.resolver = BookmakerResolver()

    async def update_due_predictions(self) -> int:
        """Найти прогнозы около старта и обновить их ссылками."""
        if not self.settings.bookmaker_link_enabled:
            return 0

        if not getattr(self.settings, "bookmaker_late_refresh_enabled", True):
            logger.info("Late bookmaker refresh disabled")
            return 0

        now = datetime.now(timezone.utc)
        before = int(getattr(self.settings, "bookmaker_late_refresh_before_start_minutes", 10) or 10)
        after = int(getattr(self.settings, "bookmaker_late_refresh_after_start_minutes", 5) or 5)

        async with SessionLocal() as session:
            rows = (await session.execute(
                select(Prediction)
                .where(Prediction.is_finished.is_(False))
                .where(Prediction.bookmaker_url == "")
                .order_by(Prediction.start_time.asc())
                .limit(20)
            )).scalars().all()

            due: list[Prediction] = []
            for prediction in rows:
                start = parse_start_time(prediction.start_time)
                if not start:
                    continue
                if now < start - timedelta(minutes=before):
                    continue
                if now > start + timedelta(minutes=after):
                    continue
                due.append(prediction)

            updated = 0
            for prediction in due:
                prediction.bookmaker_checked_at = datetime.utcnow()
                url, provider_name = await self.resolver.resolve(
                    prediction.home_team,
                    prediction.away_team,
                    prediction.start_time,
                )
                if not url:
                    logger.info(
                        "Late bookmaker refresh: exact URL not found for %s — %s",
                        prediction.home_team,
                        prediction.away_team,
                    )
                    continue

                prediction.bookmaker_url = url
                prediction.bookmaker_name = provider_name or self.settings.bookmaker_name
                prediction.bookmaker_resolved_at = datetime.utcnow()
                prediction.prediction_json = update_pick_json(
                    prediction.prediction_json,
                    bookmaker_url=url,
                )
                prediction.rendered_text = rebuild_rendered_text(prediction, url)
                await session.flush()

                await self._edit_posts(prediction)
                updated += 1

            await session.commit()
            if updated:
                logger.info("Late bookmaker refresh: updated predictions=%s", updated)
            return updated

    async def _edit_posts(self, prediction: Prediction) -> None:
        """Отредактировать приватные и публичные посты, если есть message_id."""
        private_refs = loads_refs(prediction.private_message_refs)
        public_refs = loads_refs(prediction.public_message_refs)

        for ref in private_refs:
            try:
                text = rebuild_rendered_text(prediction, prediction.bookmaker_url, lang=ref.lang)
                await self.bot.edit_message_text(
                    chat_id=ref.chat_id,
                    message_id=ref.message_id,
                    text=text[:3850] + "\n\n..." if len(text) > 3900 else text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info("Updated private bookmaker link in message chat=%s lang=%s", ref.chat_id, ref.lang)
            except Exception as exc:
                logger.warning("Failed to edit private post for prediction=%s: %s", prediction.id, exc)

        for ref in public_refs:
            try:
                detail = rebuild_rendered_text(prediction, prediction.bookmaker_url, lang=ref.lang)
                text = public_summary_from_private("", detail[:3400], lang=ref.lang)
                await self.bot.edit_message_text(
                    chat_id=ref.chat_id,
                    message_id=ref.message_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info("Updated public bookmaker link in message chat=%s lang=%s", ref.chat_id, ref.lang)
            except Exception as exc:
                logger.warning("Failed to edit public post for prediction=%s: %s", prediction.id, exc)


def parse_start_time(raw: str) -> datetime | None:
    """Распарсить start_time из Prediction в UTC."""
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def update_pick_json(raw_json: str, bookmaker_url: str) -> str:
    """Обновить bookmaker_url внутри prediction_json."""
    try:
        data = json.loads(raw_json)
        data["bookmaker_url"] = bookmaker_url
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return raw_json


def rebuild_rendered_text(prediction: Prediction, bookmaker_url: str, lang: str = "uk") -> str:
    """Перерендерить карточку прогноза из сохранённого prediction_json."""
    lang = normalize_lang(lang)
    try:
        data = json.loads(prediction.prediction_json)
        data["bookmaker_url"] = bookmaker_url
        pick = AiPick.model_validate(data)
        return render_pick_detail(pick, ctx=None, lang=lang)
    except Exception:
        logger.exception("Failed to rebuild rendered prediction text")
        suffix = f'\n\n💵 <a href="{bookmaker_url}">Відкрити лінію букмекера</a>'
        return (prediction.rendered_text or "") + suffix
