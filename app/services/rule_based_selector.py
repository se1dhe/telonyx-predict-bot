from __future__ import annotations

from app.config import get_settings
from app.schemas import AiPick, AiSelectionResponse, CandidateContext


class RuleBasedSelector:
    """Бесплатный fallback-аналитик без OpenAI."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def select_gold_matches(self, contexts: list[CandidateContext]) -> AiSelectionResponse:
        """Выбрать лучшие матчи локальным алгоритмом."""
        scored: list[tuple[int, AiPick]] = []
        rejected: list[str] = []

        for ctx in contexts:
            pick = build_pick(ctx)
            if pick.confidence < self.settings.min_ai_confidence:
                rejected.append(
                    f"{ctx.home_team} — {ctx.away_team}: локальная уверенность {pick.confidence}/100 ниже порога"
                )
                continue

            scored.append((pick.ai_rank_score, pick))

        scored.sort(key=lambda x: x[0], reverse=True)
        return AiSelectionResponse(
            selected=[pick for _, pick in scored[: self.settings.matches_per_day]],
            rejected_summary=rejected[:10],
        )


def build_pick(ctx: CandidateContext) -> AiPick:
    """Собрать прогноз по статистическим правилам."""
    h = ctx.home_metrics
    a = ctx.away_metrics

    total_matches = max(1, h.matches + a.matches)
    btts_rate = (h.btts + a.btts) / total_matches
    over15_rate = (h.over15 + a.over15) / total_matches
    over25_rate = (h.over25 + a.over25) / total_matches

    # TeamMetrics не хранит points отдельным полем.
    # Считаем очки из W/D/L: победа = 3, ничья = 1.
    h_points = h.wins * 3 + h.draws
    a_points = a.wins * 3 + a.draws

    h_ppm = h_points / max(1, h.matches)
    a_ppm = a_points / max(1, a.matches)
    h_goal_avg = h.goals_for / max(1, h.matches)
    a_goal_avg = a.goals_for / max(1, a.matches)
    h_concede_avg = h.goals_against / max(1, h.matches)
    a_concede_avg = a.goals_against / max(1, a.matches)

    elo_diff = h.elo - a.elo if h.elo and a.elo else 0
    strength_diff = (h_ppm - a_ppm) + (elo_diff / 180.0)

    bet_code = "OVER_1_5"
    bet_label = "ТБ 1.5"
    safe_label = "ТБ 1.5"
    risky_label = "ТБ 2.5"
    predicted_winner = "Исход лучше не трогать"
    who_should_score = "Осторожнее через общий тотал"
    risk = "средний"

    if over15_rate >= 0.68 and (h_goal_avg + a_goal_avg) >= 2.1:
        bet_code = "OVER_1_5"
        bet_label = "ТБ 1.5"
        risk = "низкий"

    if over25_rate >= 0.58 and (h_goal_avg + a_goal_avg) >= 2.5 and (h_concede_avg + a_concede_avg) >= 1.6:
        bet_code = "OVER_2_5"
        bet_label = "ТБ 2.5"
        safe_label = "ТБ 1.5"
        risky_label = "ОЗ Да"
        risk = "средний"

    if btts_rate >= 0.60 and h_goal_avg >= 1.0 and a_goal_avg >= 1.0:
        bet_code = "BTTS_YES"
        bet_label = "Обе забьют — Да"
        safe_label = "ТБ 1.5"
        risky_label = "ОЗ Да + ТБ 2.5"
        risk = "средний"

    if strength_diff >= 0.75:
        bet_code = "HOME_DOUBLE_CHANCE"
        bet_label = f"{ctx.home_team} не проиграет"
        safe_label = f"{ctx.home_team} не проиграет"
        risky_label = f"{ctx.home_team} DNB"
        predicted_winner = f"{ctx.home_team} ближе к победе, но безопаснее через 1X"
        who_should_score = f"{ctx.home_team} должен забить минимум один"
        risk = "низкий"
    elif strength_diff <= -0.75:
        bet_code = "AWAY_DOUBLE_CHANCE"
        bet_label = f"{ctx.away_team} не проиграет"
        safe_label = f"{ctx.away_team} не проиграет"
        risky_label = f"{ctx.away_team} DNB"
        predicted_winner = f"{ctx.away_team} ближе к победе, но безопаснее через X2"
        who_should_score = f"{ctx.away_team} должен забить минимум один"
        risk = "низкий"

    if bet_code == "HOME_DOUBLE_CHANCE" and over15_rate >= 0.62:
        bet_code = "HOME_OR_DRAW_OVER_1_5"
        bet_label = f"{ctx.home_team} не проиграет + ТБ 1.5"
        risky_label = f"{ctx.home_team} победа + ТБ 1.5"

    if bet_code == "AWAY_DOUBLE_CHANCE" and over15_rate >= 0.62:
        bet_code = "AWAY_OR_DRAW_OVER_1_5"
        bet_label = f"{ctx.away_team} не проиграет + ТБ 1.5"
        risky_label = f"{ctx.away_team} победа + ТБ 1.5"

    confidence = calculate_confidence(ctx, over15_rate, over25_rate, btts_rate, abs(strength_diff))
    rank_score = min(100, max(1, int((ctx.pre_ai_score * 0.55) + (ctx.data_quality_score * 0.25) + confidence * 0.20)))

    expected_home, expected_away = expected_score(
        strength_diff,
        h_goal_avg,
        a_goal_avg,
        h_concede_avg,
        a_concede_avg,
    )

    if predicted_winner == "Исход лучше не трогать":
        if expected_home > expected_away:
            predicted_winner = f"{ctx.home_team} чуть ближе, но безопаснее через тотал"
        elif expected_away > expected_home:
            predicted_winner = f"{ctx.away_team} чуть ближе, но безопаснее через тотал"
        else:
            predicted_winner = "Матч выглядит равным, безопаснее рынок голов"

    if who_should_score == "Осторожнее через общий тотал":
        if h_goal_avg >= a_goal_avg:
            who_should_score = f"{ctx.home_team} выглядит вероятнее по голу, но ставка лучше через общий рынок"
        else:
            who_should_score = f"{ctx.away_team} выглядит вероятнее по голу, но ставка лучше через общий рынок"

    warnings = list(ctx.rejection_risks)

    reasoning = (
        f"Форма {ctx.home_team}: {h.wins}-{h.draws}-{h.losses}, голы {h.goals_for}:{h.goals_against}, "
        f"ТБ1.5 {h.over15}/{max(1, h.matches)}, ОЗ {h.btts}/{max(1, h.matches)}. "
        f"Форма {ctx.away_team}: {a.wins}-{a.draws}-{a.losses}, голы {a.goals_for}:{a.goals_against}, "
        f"ТБ1.5 {a.over15}/{max(1, a.matches)}, ОЗ {a.btts}/{max(1, a.matches)}. "
        f"Общий фон: ТБ1.5 {int(over15_rate * 100)}%, ТБ2.5 {int(over25_rate * 100)}%, ОЗ {int(btts_rate * 100)}%."
    )

    if h.elo and a.elo:
        reasoning += f" ClubElo: {ctx.home_team} {h.elo}, {ctx.away_team} {a.elo}."

    return AiPick(
        fixture_id=ctx.fixture_id,
        match_title=f"{ctx.home_team} — {ctx.away_team}",
        ai_rank_score=rank_score,
        predicted_winner=predicted_winner,
        who_should_score=who_should_score,
        main_bet_code=bet_code,
        main_bet_label=bet_label,
        safe_bet_label=safe_label,
        risky_bet_label=risky_label,
        risk_level=risk,
        confidence=confidence,
        expected_score=f"{expected_home}:{expected_away}",
        why_this_match_is_gold=(
            f"Матч прошёл локальный фильтр: pre_ai={ctx.pre_ai_score}, "
            f"data_quality={ctx.data_quality_score}, есть статистическая база по форме и голевым трендам."
        ),
        reasoning=reasoning,
        data_warnings=warnings[:5],
        tracking_url=ctx.tracking_url,
    )


def calculate_confidence(
    ctx: CandidateContext,
    over15_rate: float,
    over25_rate: float,
    btts_rate: float,
    strength_abs: float,
) -> int:
    """Посчитать локальную уверенность."""
    confidence = 35
    confidence += int(ctx.data_quality_score * 0.18)
    confidence += int(ctx.pre_ai_score * 0.22)
    confidence += int(over15_rate * 12)
    confidence += int(over25_rate * 8)
    confidence += int(btts_rate * 6)
    confidence += min(12, int(strength_abs * 6))

    if ctx.home_metrics.matches < 4 or ctx.away_metrics.matches < 4:
        confidence -= 8

    if not ctx.standings:
        confidence -= 4

    return max(1, min(88, confidence))


def expected_score(
    strength_diff: float,
    h_goal_avg: float,
    a_goal_avg: float,
    h_concede_avg: float,
    a_concede_avg: float,
) -> tuple[int, int]:
    """Грубый ожидаемый счёт."""
    home_x = (h_goal_avg + a_concede_avg) / 2
    away_x = (a_goal_avg + h_concede_avg) / 2

    if strength_diff >= 0.75:
        home_x += 0.35
        away_x -= 0.10
    elif strength_diff <= -0.75:
        away_x += 0.35
        home_x -= 0.10

    home_goals = round_to_score(home_x)
    away_goals = round_to_score(away_x)

    if home_goals + away_goals < 2:
        if home_x >= away_x:
            home_goals += 1
        else:
            away_goals += 1

    return home_goals, away_goals


def round_to_score(value: float) -> int:
    """Округлить ожидаемые голы в реалистичный счёт."""
    if value < 0.65:
        return 0
    if value < 1.45:
        return 1
    if value < 2.35:
        return 2
    return 3
