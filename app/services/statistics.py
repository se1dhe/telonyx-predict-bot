from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Prediction, StatsSnapshot
from app.services.render import html_escape


@dataclass
class StatsData:
    """Статистика прогнозов."""

    total_settled: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    pending: int = 0
    winrate_percent: int = 0


def evaluate_bet_status(code: str, home_score: int, away_score: int) -> str:
    """Проверить прогноз.

    Возвращает:
    - win — ставка зашла;
    - loss — ставка не зашла;
    - void — возврат, например фора 0 при ничьей.
    """
    total = home_score + away_score
    home_not_lost = home_score >= away_score
    away_not_lost = away_score >= home_score

    if code == "OVER_1_5":
        return "win" if total >= 2 else "loss"
    if code == "OVER_2_5":
        return "win" if total >= 3 else "loss"
    if code == "BTTS_YES":
        return "win" if home_score >= 1 and away_score >= 1 else "loss"
    if code == "HOME_DOUBLE_CHANCE":
        return "win" if home_not_lost else "loss"
    if code == "AWAY_DOUBLE_CHANCE":
        return "win" if away_not_lost else "loss"
    if code == "HOME_OR_DRAW_OVER_1_5":
        return "win" if home_not_lost and total >= 2 else "loss"
    if code == "AWAY_OR_DRAW_OVER_1_5":
        return "win" if away_not_lost and total >= 2 else "loss"

    # Фора 0 / Draw No Bet: при ничьей это возврат, а не проигрыш.
    if code == "HOME_DNB":
        if home_score > away_score:
            return "win"
        if home_score == away_score:
            return "void"
        return "loss"

    if code == "AWAY_DNB":
        if away_score > home_score:
            return "win"
        if home_score == away_score:
            return "void"
        return "loss"

    return "loss"


def evaluate_bet(code: str, home_score: int, away_score: int) -> bool:
    """Совместимость со старым кодом."""
    return evaluate_bet_status(code, home_score, away_score) == "win"


async def collect_stats(date_key: str | None = None) -> StatsData:
    """Собрать статистику за день или за всё время.

    Winrate считается только по рассчитанным ставкам:
    win / (win + loss).
    Возвраты не портят winrate.
    Pending отдельно.
    """
    async with SessionLocal() as session:
        finished_query = select(Prediction).where(Prediction.is_finished.is_(True))
        pending_query = select(func.count(Prediction.id)).where(Prediction.is_finished.is_(False))

        if date_key:
            finished_query = finished_query.where(Prediction.date_key == date_key)
            pending_query = pending_query.where(Prediction.date_key == date_key)

        predictions = (await session.execute(finished_query)).scalars().all()
        pending = (await session.execute(pending_query)).scalar() or 0

    wins = sum(1 for p in predictions if p.is_success is True)
    losses = sum(1 for p in predictions if p.is_success is False)
    voids = sum(1 for p in predictions if p.is_success is None)
    settled = wins + losses
    winrate = int(round((wins / settled) * 100)) if settled else 0

    return StatsData(
        total_settled=settled,
        wins=wins,
        losses=losses,
        voids=voids,
        pending=pending,
        winrate_percent=winrate,
    )


async def save_stats_snapshot() -> StatsSnapshot:
    """Пересчитать и сохранить общую статистику."""
    stats = await collect_stats()
    async with SessionLocal() as session:
        snap = StatsSnapshot(
            total_predictions=stats.total_settled,
            successful_predictions=stats.wins,
            failed_predictions=stats.losses,
            winrate_percent=stats.winrate_percent,
        )
        session.add(snap)
        await session.commit()
        await session.refresh(snap)
        return snap


def render_stats_line(snapshot: StatsSnapshot) -> str:
    """Короткая строка общей статистики."""
    return (
        f"📊 <b>Статистика:</b> "
        f"{snapshot.successful_predictions}/{snapshot.total_predictions} успешных, "
        f"минусов: {snapshot.failed_predictions}, "
        f"winrate: {snapshot.winrate_percent}%"
    )


def render_stats_data_line(label: str, stats: StatsData) -> str:
    """Строка статистики для отчёта."""
    return (
        f"<b>{html_escape(label)}</b>\n"
        f"✅ Плюсов: <b>{stats.wins}</b>\n"
        f"❌ Минусов: <b>{stats.losses}</b>\n"
        f"↩️ Возвратов: <b>{stats.voids}</b>\n"
        f"⏳ Ожидают результата: <b>{stats.pending}</b>\n"
        f"📈 Winrate: <b>{stats.winrate_percent}%</b>"
    )


async def render_daily_end_stats_report(date_key: str) -> str:
    """Финальный отчёт в конце игрового дня."""
    daily = await collect_stats(date_key=date_key)
    all_time = await collect_stats(date_key=None)

    return (
        f"📊 <b>Итоги прогнозов за {html_escape(date_key)}</b>\n\n"
        f"{render_stats_data_line('За день', daily)}\n\n"
        f"{render_stats_data_line('За всё время', all_time)}\n\n"
        f"ℹ️ Winrate считается только по рассчитанным ставкам: "
        f"<b>плюсы / (плюсы + минусы)</b>. Возвраты не считаются ни плюсом, ни минусом."
    )
