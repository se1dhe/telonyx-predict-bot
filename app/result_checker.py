from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Bot
from sqlalchemy import select
from app.config import get_settings
from app.db import SessionLocal
from app.models import Prediction
from app.schemas import AiPick
from app.services.api_football import ApiFootballClient
from app.services.free_data_provider import FreeDataProvider
from app.services.render import render_result_line
from app.services.statistics import evaluate_bet, render_stats_line, save_stats_snapshot

class ResultChecker:
    """Проверка результатов."""
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.api_provider = ApiFootballClient()
        self.local_provider = FreeDataProvider()

    async def check_open_predictions(self) -> None:
        async with SessionLocal() as session:
            predictions = (await session.execute(select(Prediction).where(Prediction.is_finished.is_(False)))).scalars().all()
        if not predictions:
            return
        messages = []
        for p in predictions:
            try:
                score = await self._fetch_score(p)
                if score is None:
                    continue
                hs, as_ = score
                success = evaluate_bet(p.main_bet_code, hs, as_)
                pick = AiPick.model_validate_json(p.prediction_json)
                line = render_result_line(pick.match_title, f"{hs}:{as_}", pick.main_bet_label, success)
                await self._mark_prediction(p.id, hs, as_, success, line)
                messages.append(line)
            except Exception:
                continue
        if messages:
            snap = await save_stats_snapshot()
            text = "📌 <b>Итоги проверенных прогнозов</b>\n\n" + "\n\n".join(messages) + "\n\n" + render_stats_line(snap)
            await self.bot.send_message(self.settings.telegram_target_chat_id, text, disable_web_page_preview=True)

    async def _fetch_score(self, prediction: Prediction) -> tuple[int, int] | None:
        if prediction.provider == "API_FOOTBALL":
            fixture = await self.api_provider.fixture_by_id(prediction.fixture_id)
            if not fixture or fixture.get("fixture", {}).get("status", {}).get("short") not in {"FT","AET","PEN"}:
                return None
            goals = fixture.get("goals", {})
            if goals.get("home") is None or goals.get("away") is None:
                return None
            return int(goals["home"]), int(goals["away"])
        target_date = parse_prediction_date(prediction.date_key, self.settings.tz)
        return await self.local_provider.result_for_prediction(prediction.fixture_id, prediction.source_league_code, prediction.home_team, prediction.away_team, target_date)

    async def _mark_prediction(self, prediction_id: int, home_score: int, away_score: int, success: bool, result_text: str) -> None:
        async with SessionLocal() as session:
            p = (await session.execute(select(Prediction).where(Prediction.id == prediction_id))).scalar_one()
            p.is_finished = True; p.is_success = success
            p.final_home_score = home_score; p.final_away_score = away_score
            p.result_checked_at = datetime.utcnow(); p.result_text = result_text
            await session.commit()

def parse_prediction_date(date_key: str, tz: str):
    try:
        return datetime.strptime(date_key, "%Y-%m-%d").date()
    except ValueError:
        return datetime.now(ZoneInfo(tz)).date()
