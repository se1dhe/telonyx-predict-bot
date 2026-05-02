from __future__ import annotations
from app.schemas import CandidateContext, TeamMetrics

def apply_match_to_metrics(metrics: TeamMetrics, gf: int, ga: int, opponent: str) -> None:
    """Добавить один матч в метрики команды."""
    metrics.matches += 1
    metrics.goals_for += gf
    metrics.goals_against += ga
    if gf > ga:
        metrics.wins += 1; mark = "W"
    elif gf == ga:
        metrics.draws += 1; mark = "D"
    else:
        metrics.losses += 1; mark = "L"
    if gf >= 1 and ga >= 1:
        metrics.btts += 1
    if gf + ga >= 2:
        metrics.over15 += 1
    if gf + ga >= 3:
        metrics.over25 += 1
    if ga == 0:
        metrics.clean_sheets += 1
    if gf == 0:
        metrics.failed_to_score += 1
    metrics.last_results.append(f"{mark} {gf}:{ga} vs {opponent}")

def calculate_data_quality(ctx: CandidateContext) -> int:
    """Оценка качества данных."""
    score = 0
    score += min(ctx.home_metrics.matches, 6) * 5
    score += min(ctx.away_metrics.matches, 6) * 5
    score += min(len(ctx.h2h), 5) * 3
    if ctx.standings:
        score += 15
    if ctx.odds:
        score += 10
    if ctx.injuries:
        score += 5
    if ctx.home_metrics.elo and ctx.away_metrics.elo:
        score += 10
    return min(score, 100)

def calculate_pre_ai_score(ctx: CandidateContext) -> int:
    """Предварительная оценка перспективности."""
    h = ctx.home_metrics
    a = ctx.away_metrics
    total = max(1, h.matches + a.matches)
    btts_rate = (h.btts + a.btts) / total
    over25_rate = (h.over25 + a.over25) / total
    over15_rate = (h.over15 + a.over15) / total
    failed_rate = (h.failed_to_score + a.failed_to_score) / total
    score = ctx.data_quality_score // 2
    score += int(btts_rate * 25)
    score += int(over25_rate * 25)
    score += int(over15_rate * 10)
    score -= int(failed_rate * 18)
    if h.elo and a.elo:
        diff = abs(h.elo - a.elo)
        if 40 <= diff <= 220:
            score += 8
    return max(0, min(score, 100))

def detect_rejection_risks(ctx: CandidateContext, min_form_matches: int = 4) -> list[str]:
    """Причины сомнительности."""
    risks = []
    if ctx.home_metrics.matches < min_form_matches:
        risks.append("мало последних матчей у хозяев")
    if ctx.away_metrics.matches < min_form_matches:
        risks.append("мало последних матчей у гостей")
    if ctx.data_quality_score < 35:
        risks.append("низкое качество данных")
    if not ctx.standings:
        risks.append("нет таблицы/локального аналога таблицы")
    if not ctx.odds:
        risks.append("нет коэффициентов")
    return risks
