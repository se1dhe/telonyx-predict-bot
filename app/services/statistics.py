from __future__ import annotations
from sqlalchemy import func, select
from app.db import SessionLocal
from app.models import Prediction, StatsSnapshot

def evaluate_bet(code: str, home_score: int, away_score: int) -> bool:
    """Проверить ставку."""
    total = home_score + away_score
    home_not_lost = home_score >= away_score
    away_not_lost = away_score >= home_score
    if code == "OVER_1_5": return total >= 2
    if code == "OVER_2_5": return total >= 3
    if code == "BTTS_YES": return home_score >= 1 and away_score >= 1
    if code == "HOME_DOUBLE_CHANCE": return home_not_lost
    if code == "AWAY_DOUBLE_CHANCE": return away_not_lost
    if code == "HOME_OR_DRAW_OVER_1_5": return home_not_lost and total >= 2
    if code == "AWAY_OR_DRAW_OVER_1_5": return away_not_lost and total >= 2
    if code == "HOME_DNB": return home_score > away_score
    if code == "AWAY_DNB": return away_score > home_score
    return False

async def save_stats_snapshot() -> StatsSnapshot:
    """Пересчитать статистику."""
    async with SessionLocal() as session:
        total = (await session.execute(select(func.count(Prediction.id)).where(Prediction.is_finished.is_(True)))).scalar() or 0
        success = (await session.execute(select(func.count(Prediction.id)).where(Prediction.is_finished.is_(True)).where(Prediction.is_success.is_(True)))).scalar() or 0
        failed = (await session.execute(select(func.count(Prediction.id)).where(Prediction.is_finished.is_(True)).where(Prediction.is_success.is_(False)))).scalar() or 0
        winrate = int(round((success / total) * 100)) if total else 0
        snap = StatsSnapshot(total_predictions=total, successful_predictions=success, failed_predictions=failed, winrate_percent=winrate)
        session.add(snap); await session.commit(); await session.refresh(snap); return snap

def render_stats_line(snapshot: StatsSnapshot) -> str:
    """Строка статистики."""
    return f"📊 <b>Статистика:</b> {snapshot.successful_predictions}/{snapshot.total_predictions} успешных, ошибок: {snapshot.failed_predictions}, winrate: {snapshot.winrate_percent}%"
