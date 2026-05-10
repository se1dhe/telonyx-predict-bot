from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import desc, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Prediction
from app.schemas import AiPick, CandidateContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScopeStats:
    """Агрегированная статистика успешности для конкретного признака."""

    wins: int = 0
    losses: int = 0

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def winrate(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.wins / self.total


class LearningCalibrator:
    """Самообучающийся калибратор прогнозов на базе закрытых ставок.

    Он не делает вид, что у нас уже есть полноценная ML-модель. На старте это
    безопасный online-learning слой: бот смотрит, какие рынки/лиги/страны уже
    давали плюс или минус, и до публикации штрафует слабые исторические паттерны.
    Чем больше закрытых прогнозов в PostgreSQL, тем сильнее становится фильтр.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def apply(self, picks: list[AiPick], contexts: list[CandidateContext]) -> tuple[list[AiPick], list[str]]:
        """Вернуть откалиброванные прогнозы и причины отказов."""
        if not self.settings.learning_enabled or not picks:
            return picks, []

        history = await self._load_history()
        if not history:
            logger.info("Learning calibrator: no finished predictions yet, skip calibration")
            return self._apply_static_safety(picks, contexts), []

        stats = self._build_stats(history)
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}
        calibrated: list[AiPick] = []
        rejected: list[str] = []

        for pick in picks:
            ctx = ctx_by_id.get(pick.fixture_id)
            updated, reasons = self._calibrate_pick(pick, ctx, stats)

            if updated is None:
                rejected.append(f"{pick.match_title}: " + "; ".join(reasons))
                continue

            calibrated.append(updated)

        calibrated.sort(key=lambda p: p.ai_rank_score, reverse=True)
        result = calibrated[: self.settings.matches_per_day]

        logger.info(
            "Learning calibrator: input=%s output=%s rejected=%s history=%s",
            len(picks),
            len(result),
            len(rejected),
            len(history),
        )
        for item in rejected[:10]:
            logger.info("Learning calibrator rejected: %s", item)

        return result, rejected[:10]

    def _apply_static_safety(self, picks: list[AiPick], contexts: list[CandidateContext]) -> list[AiPick]:
        """Даже без истории применить базовый безопасный режим."""
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}
        result: list[AiPick] = []
        for pick in picks:
            updated, _ = self._calibrate_pick(pick, ctx_by_id.get(pick.fixture_id), {})
            if updated is not None:
                result.append(updated)
        return result

    async def _load_history(self) -> list[Prediction]:
        """Загрузить последние закрытые прогнозы из базы."""
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Prediction)
                    .where(Prediction.is_finished.is_(True))
                    .where(Prediction.is_success.is_not(None))
                    .order_by(desc(Prediction.created_at))
                    .limit(max(20, self.settings.learning_recent_limit))
                )
            ).scalars().all()
        return list(rows)

    def _build_stats(self, history: list[Prediction]) -> dict[str, ScopeStats]:
        """Построить статистику по рынкам, лигам, странам и связкам."""
        raw: dict[str, list[int]] = defaultdict(lambda: [0, 0])

        for row in history:
            win = bool(row.is_success)
            scopes = [
                f"bet:{row.main_bet_code}",
                f"league:{self._norm(row.league_name)}",
                f"country:{self._norm(row.country)}",
                f"bet_league:{row.main_bet_code}:{self._norm(row.league_name)}",
                f"bet_country:{row.main_bet_code}:{self._norm(row.country)}",
            ]
            for scope in scopes:
                if win:
                    raw[scope][0] += 1
                else:
                    raw[scope][1] += 1

        return {key: ScopeStats(wins=value[0], losses=value[1]) for key, value in raw.items()}

    def _calibrate_pick(
        self,
        pick: AiPick,
        ctx: CandidateContext | None,
        stats: dict[str, ScopeStats],
    ) -> tuple[AiPick | None, list[str]]:
        """Откалибровать один прогноз."""
        allowed_bets = self.settings.safe_mode_allowed_bets
        reasons: list[str] = []
        confidence = int(pick.confidence or 0)
        rank = int(pick.ai_rank_score or 0)
        warnings = list(pick.data_warnings or [])

        if self.settings.safe_mode_enabled:
            if pick.main_bet_code not in allowed_bets:
                penalty = self.settings.safe_mode_disallowed_bet_penalty
                confidence -= penalty
                rank -= penalty
                reasons.append(f"рынок {pick.main_bet_code} не входит в safe-mode")

            if not self.settings.safe_mode_allow_high_risk and str(pick.risk_level).lower().strip() == "високий":
                return None, ["высокий риск запрещён safe-mode"]

        if ctx is not None:
            min_form = max(1, self.settings.learning_min_team_form_matches)
            if ctx.home_metrics.matches < min_form or ctx.away_metrics.matches < min_form:
                confidence -= 10
                rank -= 10
                reasons.append(f"мало формы: {ctx.home_metrics.matches}/{ctx.away_metrics.matches}")

            if ctx.data_quality_score < self.settings.min_context_data_quality:
                confidence -= 8
                rank -= 8
                reasons.append(f"data_quality={ctx.data_quality_score}")

        scopes = self._scopes_for(pick, ctx)
        for scope in scopes:
            scope_stats = stats.get(scope)
            if not scope_stats or scope_stats.total < self.settings.learning_min_sample_size:
                continue

            winrate_percent = int(scope_stats.winrate * 100)
            threshold = self._threshold_for_scope(scope)

            if winrate_percent < threshold:
                penalty = self._penalty(threshold, winrate_percent)
                confidence -= penalty
                rank -= penalty
                reasons.append(f"{scope} winrate={winrate_percent}% sample={scope_stats.total}")
            elif winrate_percent >= threshold + 12:
                bonus = min(self.settings.learning_max_bonus, int((winrate_percent - threshold) * self.settings.learning_bonus_strength))
                confidence += bonus
                rank += bonus

        confidence = max(1, min(100, confidence))
        rank = max(1, min(100, rank))

        if confidence < self.settings.min_ai_confidence:
            return None, reasons or [f"confidence после обучения {confidence} ниже порога"]

        if reasons:
            warnings.append("Калібратор знизив оцінку: " + "; ".join(reasons[:3]))

        return pick.model_copy(
            update={
                "confidence": confidence,
                "ai_rank_score": rank,
                "data_warnings": warnings[:6],
            }
        ), reasons

    def _scopes_for(self, pick: AiPick, ctx: CandidateContext | None) -> list[str]:
        league = self._norm(ctx.league_name if ctx else "")
        country = self._norm(ctx.country if ctx else "")
        return [
            f"bet:{pick.main_bet_code}",
            f"league:{league}",
            f"country:{country}",
            f"bet_league:{pick.main_bet_code}:{league}",
            f"bet_country:{pick.main_bet_code}:{country}",
        ]

    def _threshold_for_scope(self, scope: str) -> int:
        if scope.startswith("bet:"):
            return self.settings.learning_min_bet_winrate
        if scope.startswith("league:"):
            return self.settings.learning_min_league_winrate
        if scope.startswith("country:"):
            return self.settings.learning_min_country_winrate
        return self.settings.learning_min_combo_winrate

    def _penalty(self, threshold: int, actual: int) -> int:
        gap = max(0, threshold - actual)
        return min(self.settings.learning_max_penalty, max(4, int(gap * self.settings.learning_penalty_strength)))

    @staticmethod
    def _norm(value: str) -> str:
        return (value or "").strip().lower() or "unknown"
