from __future__ import annotations

from dataclasses import dataclass

from app.db import SessionLocal
from app.models import Prediction, StatsSnapshot
from app.services.render import html_escape

# Эти коды никогда не должны попадать в winrate.
# Даже если старый AI/старая БД содержат что-то похожее на прогноз счёта,
# такой прогноз будет исключён из wins/losses/pending.
NON_STAT_BET_CODES = {
    "",
    "NO_BET",
    "SCORE",
    "EXACT_SCORE",
    "CORRECT_SCORE",
    "PREDICTED_SCORE",
    "EXPECTED_SCORE",
}


@dataclass
class StatsData:
    """Статистика прогнозов, которые можно честно считать в winrate."""

    total_settled: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    pending: int = 0
    ignored: int = 0
    winrate_percent: int = 0


def is_prediction_countable(prediction: Prediction) -> bool:
    """Проверить, можно ли учитывать прогноз в статистике.

    Ставки/прогнозы на счёт исключаем полностью:
    - не идут в wins;
    - не идут в losses;
    - не висят как pending;
    - не влияют на дневной и общий winrate.
    """
    code = str(getattr(prediction, "main_bet_code", "") or "").strip().upper()
    label = str(getattr(prediction, "main_bet_label", "") or "").strip().lower()

    if code in NON_STAT_BET_CODES:
        return False

    score_markers = (
        "точный счёт",
        "точный счет",
        "точний рахунок",
        "правильный счёт",
        "правильный счет",
        "correct score",
        "exact score",
        "expected score",
        "ожидаемый счёт",
        "ожидаемый счет",
        "очікуваний рахунок",
    )

    return not any(marker in label for marker in score_markers)


async def collect_stats(date_key: str | None = None) -> StatsData:
    """Собрать статистику только по нормальным рынкам ставок.

    Winrate считается так: win / (win + loss).
    Возвраты не портят winrate.
    Ставки/прогнозы на счёт полностью игнорируются.
    """
    from sqlalchemy import select

    async with SessionLocal() as session:
        stmt = select(Prediction)
        if date_key:
            stmt = stmt.where(Prediction.date_key == date_key)

        predictions = (await session.execute(stmt)).scalars().all()

    countable = [p for p in predictions if is_prediction_countable(p)]
    ignored = len(predictions) - len(countable)

    wins = sum(1 for p in countable if p.is_finished and p.is_success is True)
    losses = sum(1 for p in countable if p.is_finished and p.is_success is False)
    voids = sum(1 for p in countable if p.is_finished and p.is_success is None)
    pending = sum(1 for p in countable if not p.is_finished)

    settled = wins + losses
    winrate = int(round((wins / settled) * 100)) if settled else 0

    return StatsData(
        total_settled=settled,
        wins=wins,
        losses=losses,
        voids=voids,
        pending=pending,
        ignored=ignored,
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


async def can_publish_after_match_daily_stats(date_key: str) -> bool:
    """Разрешить дневной winrate только когда все countable-матчи дня закрыты."""
    daily = await collect_stats(date_key=date_key)
    total_for_day = daily.wins + daily.losses + daily.voids + daily.pending

    if total_for_day <= 0:
        return False

    return daily.pending == 0


async def can_publish_daily_end_report(date_key: str) -> bool:
    """Разрешить финальный отчёт только когда нет ожидающих countable-матчей."""
    return await can_publish_after_match_daily_stats(date_key)


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
            f"ℹ️ Formula: <b>won / (won + lost)</b>. Void and score picks are excluded."
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
            f"ℹ️ Формула: <b>зашло / (зашло + не зашло)</b>. Возвраты и прогнозы счёта не учитываются."
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
        f"ℹ️ Формула: <b>зайшло / (зайшло + не зайшло)</b>. Повернення та прогнози рахунку не враховуються."
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
            f"ℹ️ Formula: <b>won / (won + lost)</b>. Void and score picks are excluded."
        )

    if lang == "ru":
        return (
            f"📊 <b>Дневной winrate за {html_escape(date_key)}</b>\n\n"
            f"✅ Зашло: <b>{daily.wins}</b>\n"
            f"❌ Не зашло: <b>{daily.losses}</b>\n"
            f"↩️ Возврат: <b>{daily.voids}</b>\n"
            f"⏳ Ещё ждём: <b>{daily.pending}</b>\n"
            f"📈 Winrate дня: <b>{daily.winrate_percent}%</b>\n\n"
            f"ℹ️ Формула: <b>зашло / (зашло + не зашло)</b>. Возвраты и прогнозы счёта не учитываются."
        )

    return (
        f"📊 <b>Денний winrate за {html_escape(date_key)}</b>\n\n"
        f"✅ Зайшло: <b>{daily.wins}</b>\n"
        f"❌ Не зайшло: <b>{daily.losses}</b>\n"
        f"↩️ Повернення: <b>{daily.voids}</b>\n"
        f"⏳ Ще очікуємо: <b>{daily.pending}</b>\n"
        f"📈 Winrate дня: <b>{daily.winrate_percent}%</b>\n\n"
        f"ℹ️ Формула: <b>зайшло / (зайшло + не зайшло)</b>. Повернення та прогнози рахунку не враховуються."
    )


async def render_after_match_daily_stats_report_if_complete(date_key: str, lang: str = "uk") -> str | None:
    """Вернуть дневной winrate только если все countable-матчи дня закрыты."""
    if not await can_publish_after_match_daily_stats(date_key):
        return None

    return await render_after_match_daily_stats_report(date_key=date_key, lang=lang)


def evaluate_bet_status(bet_code: str, home_score: int, away_score: int) -> str:
    """Оценить исход ставки по финальному счёту матча.

    Счёт используется только технически для рынков ТБ/ОЗ/двойной шанс/DNB.
    Сами прогнозы на точный счёт не поддерживаются и возвращают void.
    """
    code = str(bet_code or "").strip().upper()
    total = home_score + away_score

    if code in NON_STAT_BET_CODES:
        return "void"

    if code == "OVER_1_5":
        return "win" if total > 1.5 else "loss"

    if code == "OVER_2_5":
        return "win" if total > 2.5 else "loss"

    if code == "BTTS_YES":
        return "win" if home_score > 0 and away_score > 0 else "loss"

    if code in {"HOME_DOUBLE_CHANCE", "HOME_OR_DRAW_OVER_1_5"}:
        ok_side = home_score >= away_score
        if code == "HOME_OR_DRAW_OVER_1_5":
            return "win" if ok_side and total > 1.5 else "loss"
        return "win" if ok_side else "loss"

    if code in {"AWAY_DOUBLE_CHANCE", "AWAY_OR_DRAW_OVER_1_5"}:
        ok_side = away_score >= home_score
        if code == "AWAY_OR_DRAW_OVER_1_5":
            return "win" if ok_side and total > 1.5 else "loss"
        return "win" if ok_side else "loss"

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

    return "void"
