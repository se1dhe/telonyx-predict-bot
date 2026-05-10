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
    """Observe-only слой обучения + отдельная статическая защита safe-mode.

    Важно: пока обучаемая модель НЕ имеет права управлять публикацией.
    Она только читает закрытые прогнозы, строит статистику и пишет выводы в Railway Logs.

    За реальные прогнозы отвечают:
    - AI / rule-based selector;
    - статический safe-mode, который не зависит от истории winrate.

    Такой режим нужен, чтобы модель спокойно набирала историю, но не могла:
    - снижать confidence;
    - повышать confidence;
    - менять rank;
    - выбрасывать матч из-за исторической статистики;
    - брать весь отбор на себя.
    """

    GOAL_MARKETS_TO_OVER_15 = {
        "BTTS_YES",
        "OVER_2_5",
        "HOME_OR_DRAW_OVER_1_5",
        "AWAY_OR_DRAW_OVER_1_5",
    }

    def __init__(self) -> None:
        self.settings = get_settings()

    async def apply(self, picks: list[AiPick], contexts: list[CandidateContext]) -> tuple[list[AiPick], list[str]]:
        """Вернуть прогнозы после safe-mode, а learning оставить в режиме наблюдения."""
        if not picks:
            return picks, []

        if self.settings.learning_enabled:
            await self._observe_history_only(picks, contexts)
        else:
            logger.info("Learning observer: disabled by LEARNING_ENABLED=false")

        safe_picks, safe_rejected = self._apply_static_safety(picks)
        return safe_picks[: self.settings.matches_per_day], safe_rejected[:10]

    async def _observe_history_only(self, picks: list[AiPick], contexts: list[CandidateContext]) -> None:
        """Прочитать историю и записать диагностическую статистику без влияния на picks."""
        history = await self._load_history()
        if not history:
            logger.info("Learning observer: no finished predictions yet; selection is not changed")
            return

        stats = self._build_stats(history)
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}

        logger.info(
            "Learning observer: history=%s current_picks=%s mode=observe_only selection_changed=false",
            len(history),
            len(picks),
        )

        for pick in picks:
            ctx = ctx_by_id.get(pick.fixture_id)
            scopes = self._scopes_for(pick, ctx)
            scope_notes = []

            for scope in scopes:
                scope_stats = stats.get(scope)
                if not scope_stats or scope_stats.total < self.settings.learning_min_sample_size:
                    continue

                scope_notes.append(
                    f"{scope}=winrate:{int(scope_stats.winrate * 100)}%,sample:{scope_stats.total}"
                )

            if scope_notes:
                logger.info(
                    "Learning observer pick: %s | %s | no score/rank/filter changes applied",
                    pick.match_title,
                    "; ".join(scope_notes[:5]),
                )

    def _apply_static_safety(self, picks: list[AiPick]) -> tuple[list[AiPick], list[str]]:
        """Применить safe-mode без участия обучающей статистики."""
        if not self.settings.safe_mode_enabled:
            return picks, []

        allowed_bets = set(self.settings.safe_mode_allowed_bets)
        result: list[AiPick] = []
        rejected: list[str] = []

        for pick in picks:
            if self._is_high_risk_blocked(pick):
                rejected.append(f"{pick.match_title}: Safe-mode відхилив високий ризик")
                continue

            safe_pick, reason = self._normalize_bet_for_safe_mode(pick, allowed_bets)
            if safe_pick is None:
                rejected.append(
                    f"{pick.match_title}: Safe-mode відхилив ринок {pick.main_bet_code}, дозволено тільки {', '.join(sorted(allowed_bets))}"
                )
                continue

            if reason:
                logger.info("Safe-mode normalized pick: %s | %s", pick.match_title, reason)

            result.append(safe_pick)

        logger.info(
            "Safe-mode: input=%s output=%s rejected=%s allowed=%s",
            len(picks),
            len(result),
            len(rejected),
            sorted(allowed_bets),
        )
        return result, rejected

    def _is_high_risk_blocked(self, pick: AiPick) -> bool:
        """Проверить запрет высокого риска."""
        if self.settings.safe_mode_allow_high_risk:
            return False

        risk = str(pick.risk_level or "").lower().strip()
        return risk in {"високий", "высокий", "high"}

    def _normalize_bet_for_safe_mode(self, pick: AiPick, allowed_bets: set[str]) -> tuple[AiPick | None, str]:
        """Жёстко привести ставку к разрешённым рынкам или отклонить.

        Почему не просто штраф:
        раньше BTTS_YES/OVER_2_5 могли пройти после штрафа, если confidence оставался
        выше MIN_AI_CONFIDENCE. Теперь запрещённые рынки не публикуются как есть.
        Для голевых рынков разрешён безопасный downgrade в OVER_1_5, если он включён
        в SAFE_MODE_ALLOWED_BETS.
        """
        if pick.main_bet_code in allowed_bets:
            return pick, ""

        if "OVER_1_5" in allowed_bets and pick.main_bet_code in self.GOAL_MARKETS_TO_OVER_15:
            warnings = list(pick.data_warnings or [])
            warnings.append(f"Safe-mode замінив {pick.main_bet_code} на OVER_1_5")

            penalty = max(0, int(self.settings.safe_mode_disallowed_bet_penalty or 0))
            confidence = max(
                int(self.settings.min_ai_confidence),
                int(pick.confidence or 0) - penalty,
            )
            rank = max(1, int(pick.ai_rank_score or 0) - penalty)

            return pick.model_copy(
                update={
                    "main_bet_code": "OVER_1_5",
                    "main_bet_label": "Тотал більше 1.5 гола",
                    "safe_bet_label": "Тотал більше 1.5 гола",
                    "risky_bet_label": pick.main_bet_label,
                    "risk_level": "низький" if str(pick.risk_level).lower().strip() in {"низький", "низкий", "low"} else "середній",
                    "confidence": min(100, confidence),
                    "ai_rank_score": min(100, rank),
                    "data_warnings": warnings[:6],
                }
            ), f"{pick.main_bet_code} -> OVER_1_5"

        return None, f"{pick.main_bet_code} is not allowed"

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

    def _scopes_for(self, pick: AiPick, ctx: CandidateContext | None) -> list[str]:
        """Список признаков, за которыми learning только наблюдает."""
        league = self._norm(ctx.league_name if ctx else "")
        country = self._norm(ctx.country if ctx else "")
        return [
            f"bet:{pick.main_bet_code}",
            f"league:{league}",
            f"country:{country}",
            f"bet_league:{pick.main_bet_code}:{league}",
            f"bet_country:{pick.main_bet_code}:{country}",
        ]

    @staticmethod
    def _norm(value: str) -> str:
        return (value or "").strip().lower() or "unknown"
