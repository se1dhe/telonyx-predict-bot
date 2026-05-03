from __future__ import annotations

from dataclasses import dataclass

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


async def collect_stats(date_key: str | None = None) -> StatsData:
    """Собрать статистику прогнозов.

    Winrate считается только по рассчитанным ставкам:
    win / (win + loss). Возвраты не портят winrate.
    """
    from sqlalchemy import select

    async with SessionLocal() as session:
        stmt = select(Prediction)
        if date_key:
            stmt = stmt.where(Prediction.date_key == date_key)

        predictions = (await session.execute(stmt)).scalars().all()

    wins = sum(1 for p in predictions if p.is_finished and p.is_success is True)
    losses = sum(1 for p in predictions if p.is_finished and p.is_success is False)
    voids = sum(1 for p in predictions if p.is_finished and p.is_success is None)
    pending = sum(1 for p in predictions if not p.is_finished)

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
        snapshot = StatsSnapshot(
            total_predictions=stats.total_settled + stats.voids + stats.pending,
            successful_predictions=stats.wins,
            failed_predictions=stats.losses,
            winrate_percent=stats.winrate_percent,
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot


def render_stats_snapshot(snapshot: StatsSnapshot, lang: str = "uk") -> str:
    """Короткая строка общей статистики."""
    if lang == "en":
        return (
            f"total: {snapshot.total_predictions}, "
            f"won: {snapshot.successful_predictions}, "
            f"lost: {snapshot.failed_predictions}, "
            f"winrate: {snapshot.winrate_percent}%"
        )
    if lang == "ru":
        return (
            f"всего: {snapshot.total_predictions}, "
            f"зашло: {snapshot.successful_predictions}, "
            f"не зашло: {snapshot.failed_predictions}, "
            f"winrate: {snapshot.winrate_percent}%"
        )
    return (
        f"усього: {snapshot.total_predictions}, "
        f"зайшло: {snapshot.successful_predictions}, "
        f"не зайшло: {snapshot.failed_predictions}, "
        f"winrate: {snapshot.winrate_percent}%"
    )


async def render_full_stats_report(date_key: str, lang: str = "uk") -> str:
    """Полный отчёт: день + всё время."""
    daily = await collect_stats(date_key=date_key)
    total = await collect_stats()

    if lang == "en":
        return (
            f"📊 <b>Prediction results for {html_escape(date_key)}</b>\n\n"
            f"✅ Won: <b>{daily.wins}</b>\n"
            f"❌ Lost: <b>{daily.losses}</b>\n"
            f"↩️ Void: <b>{daily.voids}</b>\n"
            f"⏳ Pending: <b>{daily.pending}</b>\n"
            f"📈 Daily winrate: <b>{daily.winrate_percent}%</b>\n\n"
            f"🌐 <b>All-time stats</b>\n"
            f"✅ Won: <b>{total.wins}</b>\n"
            f"❌ Lost: <b>{total.losses}</b>\n"
            f"↩️ Void: <b>{total.voids}</b>\n"
            f"⏳ Pending: <b>{total.pending}</b>\n"
            f"📈 Total winrate: <b>{total.winrate_percent}%</b>\n\n"
            f"ℹ️ Formula: <b>won / (won + lost)</b>. Void picks are excluded."
        )

    if lang == "ru":
        return (
            f"📊 <b>Итоги прогнозов за {html_escape(date_key)}</b>\n\n"
            f"✅ Зашло: <b>{daily.wins}</b>\n"
            f"❌ Не зашло: <b>{daily.losses}</b>\n"
            f"↩️ Возврат: <b>{daily.voids}</b>\n"
            f"⏳ Ожидают результата: <b>{daily.pending}</b>\n"
            f"📈 Winrate дня: <b>{daily.winrate_percent}%</b>\n\n"
            f"🌐 <b>Статистика за всё время</b>\n"
            f"✅ Зашло: <b>{total.wins}</b>\n"
            f"❌ Не зашло: <b>{total.losses}</b>\n"
            f"↩️ Возврат: <b>{total.voids}</b>\n"
            f"⏳ Ожидают результата: <b>{total.pending}</b>\n"
            f"📈 Общий winrate: <b>{total.winrate_percent}%</b>\n\n"
            f"ℹ️ Формула: <b>зашло / (зашло + не зашло)</b>. Возвраты не учитываются."
        )

    return (
        f"📊 <b>Підсумки прогнозів за {html_escape(date_key)}</b>\n\n"
        f"✅ Зайшло: <b>{daily.wins}</b>\n"
        f"❌ Не зайшло: <b>{daily.losses}</b>\n"
        f"↩️ Повернення: <b>{daily.voids}</b>\n"
        f"⏳ Очікують результату: <b>{daily.pending}</b>\n"
        f"📈 Winrate дня: <b>{daily.winrate_percent}%</b>\n\n"
        f"🌐 <b>Статистика за весь час</b>\n"
        f"✅ Зайшло: <b>{total.wins}</b>\n"
        f"❌ Не зайшло: <b>{total.losses}</b>\n"
        f"↩️ Повернення: <b>{total.voids}</b>\n"
        f"⏳ Очікують результату: <b>{total.pending}</b>\n"
        f"📈 Загальний winrate: <b>{total.winrate_percent}%</b>\n\n"
        f"ℹ️ Формула: <b>зайшло / (зайшло + не зайшло)</b>. Повернення не враховуються."
    )


async def render_after_match_daily_stats_report(date_key: str, lang: str = "uk") -> str:
    """Короткий дневной winrate после завершённого матча."""
    daily = await collect_stats(date_key=date_key)

    if lang == "en":
        return (
            f"📊 <b>Daily winrate for {html_escape(date_key)}</b>\n\n"
            f"✅ Won: <b>{daily.wins}</b>\n"
            f"❌ Lost: <b>{daily.losses}</b>\n"
            f"↩️ Void: <b>{daily.voids}</b>\n"
            f"⏳ Pending: <b>{daily.pending}</b>\n"
            f"📈 Daily winrate: <b>{daily.winrate_percent}%</b>\n\n"
            f"ℹ️ Formula: <b>won / (won + lost)</b>. Void picks are excluded."
        )

    if lang == "ru":
        return (
            f"📊 <b>Дневной winrate за {html_escape(date_key)}</b>\n\n"
            f"✅ Зашло: <b>{daily.wins}</b>\n"
            f"❌ Не зашло: <b>{daily.losses}</b>\n"
            f"↩️ Возврат: <b>{daily.voids}</b>\n"
            f"⏳ Ещё ждём: <b>{daily.pending}</b>\n"
            f"📈 Winrate дня: <b>{daily.winrate_percent}%</b>\n\n"
            f"ℹ️ Формула: <b>зашло / (зашло + не зашло)</b>. Возвраты не учитываются."
        )

    return (
        f"📊 <b>Денний winrate за {html_escape(date_key)}</b>\n\n"
        f"✅ Зайшло: <b>{daily.wins}</b>\n"
        f"❌ Не зайшло: <b>{daily.losses}</b>\n"
        f"↩️ Повернення: <b>{daily.voids}</b>\n"
        f"⏳ Ще очікуємо: <b>{daily.pending}</b>\n"
        f"📈 Winrate дня: <b>{daily.winrate_percent}%</b>\n\n"
        f"ℹ️ Формула: <b>зайшло / (зайшло + не зайшло)</b>. Повернення не враховуються."
    )


def evaluate_bet_status(bet_code: str, home_score: int, away_score: int) -> str:
    """Оценить исход ставки.

    Возвращает:
    - win;
    - loss;
    - void.
    """
    total = home_score + away_score

    if bet_code == "OVER_1_5":
        return "win" if total > 1.5 else "loss"

    if bet_code == "OVER_2_5":
        return "win" if total > 2.5 else "loss"

    if bet_code == "BTTS_YES":
        return "win" if home_score > 0 and away_score > 0 else "loss"

    if bet_code in {"HOME_DOUBLE_CHANCE", "HOME_OR_DRAW_OVER_1_5"}:
        ok_side = home_score >= away_score
        if bet_code == "HOME_OR_DRAW_OVER_1_5":
            return "win" if ok_side and total > 1.5 else "loss"
        return "win" if ok_side else "loss"

    if bet_code in {"AWAY_DOUBLE_CHANCE", "AWAY_OR_DRAW_OVER_1_5"}:
        ok_side = away_score >= home_score
        if bet_code == "AWAY_OR_DRAW_OVER_1_5":
            return "win" if ok_side and total > 1.5 else "loss"
        return "win" if ok_side else "loss"

    if bet_code == "HOME_DNB":
        if home_score > away_score:
            return "win"
        if home_score == away_score:
            return "void"
        return "loss"

    if bet_code == "AWAY_DNB":
        if away_score > home_score:
            return "win"
        if home_score == away_score:
            return "void"
        return "loss"

    return "void"
