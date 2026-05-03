from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.models import Prediction, StatsReport
from app.schemas import AiPick
from app.services.api_football import ApiFootballClient
from app.services.free_data_provider import FreeDataProvider
from app.services.render import render_result_line
from app.services.statistics import (
    evaluate_bet_status,
    render_after_match_daily_stats_report,
    render_full_stats_report as render_daily_end_stats_report,
    save_stats_snapshot,
)


logger = logging.getLogger(__name__)


class ResultChecker:
    """Проверка результатов и статистика."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.api_provider = ApiFootballClient()
        self.local_provider = FreeDataProvider()

    async def _send_to_private_channels(self, texts_by_lang: dict[str, str]) -> None:
        """Отправить сообщение во все заполненные приватные языковые каналы."""
        sent = set()
        for lang in self.settings.active_private_languages:
            chat_id = self.settings.private_channel_for(lang)
            if not chat_id or chat_id in sent:
                continue
            sent.add(chat_id)
            text = texts_by_lang.get(lang) or texts_by_lang.get("uk") or next(iter(texts_by_lang.values()))
            try:
                await self.bot.send_message(
                    chat_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.exception("Не удалось отправить статистику/результат в канал chat_id=%s: %s", chat_id, exc)

    async def check_open_predictions(self) -> int:
        """Проверить все открытые прогнозы.

        После каждого закрытого матча бот публикует:
        1. результат ставки;
        2. дневной winrate на текущий момент.

        Возвращает количество прогнозов, которые удалось закрыть сейчас.
        """
        async with SessionLocal() as session:
            predictions = (
                await session.execute(
                    select(Prediction).where(Prediction.is_finished.is_(False))
                )
            ).scalars().all()

        if not predictions:
            return 0

        closed_count = 0

        for p in predictions:
            try:
                score = await self._fetch_score(p)
                if score is None:
                    logger.info(
                        "Результат ещё не найден: prediction_id=%s fixture_id=%s match=%s — %s date=%s provider=%s",
                        p.id,
                        p.fixture_id,
                        p.home_team,
                        p.away_team,
                        p.date_key,
                        p.provider,
                    )
                    continue

                home_score, away_score = score
                status = evaluate_bet_status(p.main_bet_code, home_score, away_score)
                pick = AiPick.model_validate_json(p.prediction_json)

                # В БД сохраняем украинский результат, а в каналы отправляем языковые версии.
                stored_line = render_result_line(
                    pick.match_title,
                    f"{home_score}:{away_score}",
                    pick.main_bet_label,
                    status,
                    lang="uk",
                )

                await self._mark_prediction(
                    prediction_id=p.id,
                    home_score=home_score,
                    away_score=away_score,
                    status=status,
                    result_text=stored_line,
                )

                closed_count += 1
                logger.info(
                    "Прогноз закрыт: prediction_id=%s match=%s — %s score=%s:%s status=%s",
                    p.id,
                    p.home_team,
                    p.away_team,
                    home_score,
                    away_score,
                    status,
                )
                await save_stats_snapshot()

                texts: dict[str, str] = {}
                for lang in self.settings.render_languages:
                    title = {
                        "uk": "📌 <b>Матч завершено</b>",
                        "en": "📌 <b>Match finished</b>",
                        "ru": "📌 <b>Матч завершён</b>",
                    }.get(lang, "📌 <b>Match finished</b>")

                    line = render_result_line(
                        pick.match_title,
                        f"{home_score}:{away_score}",
                        pick.main_bet_label,
                        status,
                        lang=lang,
                    )
                    text = f"{title}\n\n{line}"

                    if self.settings.stats_after_each_finished_match_enabled:
                        daily_stats = await render_after_match_daily_stats_report(p.date_key, lang=lang)
                        text += f"\n\n{daily_stats}"

                    texts[lang] = text

                await self._send_to_private_channels(texts)

            except Exception:
                logger.exception("Не удалось проверить прогноз id=%s", p.id)

        return closed_count

    async def send_daily_stats_report(self, force: bool = False) -> bool:
        """Отправить статистику за день и за всё время.

        По умолчанию отчёт отправляется один раз в день.
        """
        date_key = datetime.now(ZoneInfo(self.settings.tz)).date().isoformat()

        # Перед финальным отчётом ещё раз пробуем закрыть результаты.
        await self.check_open_predictions()

        if not force and await self._report_exists(date_key):
            logger.info("Статистика за %s уже отправлялась", date_key)
            return False

        texts = {
            lang: await render_daily_end_stats_report(date_key, lang=lang)
            for lang in self.settings.render_languages
        }
        await self._send_to_private_channels(texts)

        await self._save_report(date_key, texts.get("uk") or next(iter(texts.values())))
        return True

    async def _fetch_score(self, prediction: Prediction) -> tuple[int, int] | None:
        """Получить финальный счёт."""
        if prediction.provider == "API_FOOTBALL":
            fixture = await self.api_provider.fixture_by_id(prediction.fixture_id)
            if not fixture:
                return None

            status = fixture.get("fixture", {}).get("status", {}).get("short")
            if status not in {"FT", "AET", "PEN"}:
                return None

            goals = fixture.get("goals", {})
            if goals.get("home") is None or goals.get("away") is None:
                return None

            return int(goals["home"]), int(goals["away"])

        target_date = parse_prediction_date(prediction.date_key, self.settings.tz)
        return await self.local_provider.result_for_prediction(
            prediction.fixture_id,
            prediction.source_league_code,
            prediction.home_team,
            prediction.away_team,
            target_date,
        )

    async def _mark_prediction(
        self,
        prediction_id: int,
        home_score: int,
        away_score: int,
        status: str,
        result_text: str,
    ) -> None:
        """Закрыть прогноз."""
        async with SessionLocal() as session:
            prediction = (
                await session.execute(
                    select(Prediction).where(Prediction.id == prediction_id)
                )
            ).scalar_one()

            prediction.is_finished = True

            if status == "win":
                prediction.is_success = True
            elif status == "loss":
                prediction.is_success = False
            else:
                # void/возврат
                prediction.is_success = None

            prediction.final_home_score = home_score
            prediction.final_away_score = away_score
            prediction.result_checked_at = datetime.utcnow()
            prediction.result_text = result_text

            await session.commit()

    async def _report_exists(self, date_key: str) -> bool:
        async with SessionLocal() as session:
            existing = (
                await session.execute(
                    select(StatsReport).where(
                        StatsReport.date_key == date_key,
                        StatsReport.report_type == "daily_end",
                    )
                )
            ).scalar_one_or_none()
            return existing is not None

    async def _save_report(self, date_key: str, text: str) -> None:
        async with SessionLocal() as session:
            session.add(
                StatsReport(
                    date_key=date_key,
                    report_type="daily_end",
                    rendered_text=text,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()


def parse_prediction_date(date_key: str, tz: str):
    try:
        return datetime.strptime(date_key, "%Y-%m-%d").date()
    except ValueError:
        return datetime.now(ZoneInfo(tz)).date()
