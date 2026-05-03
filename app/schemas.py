from __future__ import annotations
from typing import Any, Literal
from urllib.parse import quote_plus
from pydantic import BaseModel, Field

BetCode = Literal["OVER_1_5","OVER_2_5","BTTS_YES","HOME_DOUBLE_CHANCE","AWAY_DOUBLE_CHANCE","HOME_OR_DRAW_OVER_1_5","AWAY_OR_DRAW_OVER_1_5","HOME_DNB","AWAY_DNB","NO_BET"]

class TeamMetrics(BaseModel):
    """Метрики формы."""
    team_id: str
    name: str
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    btts: int = 0
    over15: int = 0
    over25: int = 0
    clean_sheets: int = 0
    failed_to_score: int = 0
    elo: int | None = None
    last_results: list[str] = Field(default_factory=list)

class CandidateContext(BaseModel):
    """Контекст матча."""
    provider: str = "LOCAL"
    fixture_id: str
    source_league_code: str = ""
    start_time: str
    home_team: str
    away_team: str
    home_team_id: str
    away_team_id: str
    league_id: str = ""
    league_name: str
    country: str
    venue: str | None = None
    home_metrics: TeamMetrics
    away_metrics: TeamMetrics
    h2h: list[dict[str, Any]] = Field(default_factory=list)
    standings: list[dict[str, Any]] = Field(default_factory=list)
    injuries: list[dict[str, Any]] = Field(default_factory=list)
    odds: list[dict[str, Any]] = Field(default_factory=list)
    news: list[dict[str, Any]] = Field(default_factory=list)
    match_url: str = ""
    data_quality_score: int = 0
    pre_ai_score: int = 0
    rejection_risks: list[str] = Field(default_factory=list)

    @property
    def tracking_url(self) -> str:
        if self.match_url:
            return self.match_url
        q = quote_plus(f"{self.home_team} {self.away_team}")
        return f"https://www.sofascore.com/search?q={q}"

class AiPick(BaseModel):
    """Финальный прогноз."""
    fixture_id: str
    match_title: str
    ai_rank_score: int = Field(ge=1, le=100)
    predicted_winner: str
    who_should_score: str
    main_bet_code: BetCode
    main_bet_label: str
    safe_bet_label: str
    risky_bet_label: str
    risk_level: str
    confidence: int = Field(ge=1, le=100)
    expected_score: str
    why_this_match_is_gold: str
    reasoning: str
    data_warnings: list[str] = Field(default_factory=list)
    tracking_url: str

class AiSelectionResponse(BaseModel):
    """Ответ AI."""
    selected: list[AiPick]
    rejected_summary: list[str] = Field(default_factory=list)
