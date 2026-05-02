from __future__ import annotations
import asyncio
from datetime import date
from typing import Any
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import get_settings
from app.schemas import CandidateContext, TeamMetrics
from app.services.scoring import apply_match_to_metrics, calculate_data_quality, calculate_pre_ai_score, detect_rejection_risks

class ApiFootballClient:
    """Клиент API-FOOTBALL / API-Sports."""
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = f"https://{self.settings.apifootball_host}"
        self.headers = {"x-apisports-key": self.settings.apifootball_key or ""}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET к API с нормальным выводом ошибок."""
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
                text = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"API-FOOTBALL HTTP {response.status}: {text[:1000]}")
                data = await response.json()
                if data.get("errors"):
                    raise RuntimeError(f"API-FOOTBALL API errors on {path} with params={params}: {data.get('errors')}")
                return data

    async def fixtures_by_date(self, target_date: date) -> list[dict[str, Any]]:
        payload = await self.get_json("/fixtures", {"date": target_date.isoformat(), "timezone": self.settings.tz})
        return payload.get("response", [])

    async def fixture_by_id(self, fixture_id: str) -> dict[str, Any] | None:
        payload = await self.get_json("/fixtures", {"id": fixture_id, "timezone": self.settings.tz})
        rows = payload.get("response", [])
        return rows[0] if rows else None

    async def team_last_fixtures(self, team_id: str, last: int = 8) -> list[dict[str, Any]]:
        payload = await self.get_json("/fixtures", {"team": team_id, "last": last})
        return payload.get("response", [])

    async def h2h(self, home_id: str, away_id: str, last: int = 5) -> list[dict[str, Any]]:
        payload = await self.get_json("/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": last})
        return payload.get("response", [])

    async def standings(self, league_id: str, season: int | None) -> list[dict[str, Any]]:
        if not season:
            return []
        try:
            payload = await self.get_json("/standings", {"league": league_id, "season": season})
        except Exception:
            return []
        rows = []
        for item in payload.get("response", []):
            for table in item.get("league", {}).get("standings", []):
                for row in table:
                    team = row.get("team", {})
                    all_data = row.get("all", {})
                    goals = all_data.get("goals", {})
                    rows.append({"rank": row.get("rank"), "team": team.get("name"), "team_id": str(team.get("id")),
                                 "points": row.get("points"), "played": all_data.get("played"),
                                 "win": all_data.get("win"), "draw": all_data.get("draw"), "lose": all_data.get("lose"),
                                 "goals_for": goals.get("for"), "goals_against": goals.get("against")})
        return rows

    async def injuries(self, fixture_id: str) -> list[dict[str, Any]]:
        try:
            payload = await self.get_json("/injuries", {"fixture": fixture_id})
            return payload.get("response", [])
        except Exception:
            return []

    async def odds(self, fixture_id: str) -> list[dict[str, Any]]:
        try:
            payload = await self.get_json("/odds", {"fixture": fixture_id})
            return payload.get("response", [])
        except Exception:
            return []

    async def build_context(self, fixture: dict[str, Any]) -> CandidateContext:
        fixture_info = fixture["fixture"]; teams = fixture["teams"]; league = fixture["league"]
        fixture_id = str(fixture_info["id"]); home_id = str(teams["home"]["id"]); away_id = str(teams["away"]["id"])
        league_id = str(league["id"]); season = league.get("season")
        home_last, away_last, h2h, standings, injuries, odds = await asyncio.gather(
            self.team_last_fixtures(home_id, 8), self.team_last_fixtures(away_id, 8),
            self.h2h(home_id, away_id, 5), self.standings(league_id, season), self.injuries(fixture_id), self.odds(fixture_id))
        ctx = CandidateContext(provider="API_FOOTBALL", fixture_id=fixture_id, source_league_code=league_id,
            start_time=fixture_info.get("date", ""), home_team=teams["home"]["name"], away_team=teams["away"]["name"],
            home_team_id=home_id, away_team_id=away_id, league_id=league_id, league_name=league.get("name", ""),
            country=league.get("country", ""), venue=fixture_info.get("venue", {}).get("name") if fixture_info.get("venue") else None,
            home_metrics=build_team_metrics(home_id, teams["home"]["name"], home_last),
            away_metrics=build_team_metrics(away_id, teams["away"]["name"], away_last),
            h2h=simplify_fixtures(h2h), standings=standings, injuries=simplify_injuries(injuries), odds=simplify_odds(odds))
        ctx.data_quality_score = calculate_data_quality(ctx)
        ctx.pre_ai_score = calculate_pre_ai_score(ctx)
        ctx.rejection_risks = detect_rejection_risks(ctx, self.settings.local_min_form_matches)
        return ctx

def build_team_metrics(team_id: str, team_name: str, fixtures: list[dict[str, Any]]) -> TeamMetrics:
    metrics = TeamMetrics(team_id=team_id, name=team_name)
    for item in fixtures:
        if item.get("fixture", {}).get("status", {}).get("short") not in {"FT", "AET", "PEN"}:
            continue
        teams = item.get("teams", {}); goals = item.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None:
            continue
        is_home = str(teams.get("home", {}).get("id")) == team_id
        gf = int(hg if is_home else ag); ga = int(ag if is_home else hg)
        opponent = teams.get("away", {}).get("name") if is_home else teams.get("home", {}).get("name")
        apply_match_to_metrics(metrics, gf, ga, opponent or "Unknown")
    return metrics

def simplify_fixtures(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in fixtures[:5]:
        result.append({"date": item.get("fixture", {}).get("date"), "home": item.get("teams", {}).get("home", {}).get("name"),
                       "away": item.get("teams", {}).get("away", {}).get("name"), "home_goals": item.get("goals", {}).get("home"),
                       "away_goals": item.get("goals", {}).get("away"), "league": item.get("league", {}).get("name")})
    return result

def simplify_injuries(injuries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"team": i.get("team", {}).get("name"), "player": i.get("player", {}).get("name"),
             "type": i.get("player", {}).get("type"), "reason": i.get("player", {}).get("reason")} for i in injuries[:20]]

def simplify_odds(odds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for event in odds[:2]:
        for bookmaker in event.get("bookmakers", [])[:3]:
            bets = []
            for bet in bookmaker.get("bets", [])[:5]:
                if bet.get("name") in {"Match Winner", "Goals Over/Under", "Both Teams Score", "Double Chance"}:
                    bets.append({"market": bet.get("name"), "values": bet.get("values", [])[:10]})
            if bets:
                result.append({"bookmaker": bookmaker.get("name"), "bets": bets})
    return result
