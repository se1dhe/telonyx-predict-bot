from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.schemas import CandidateContext, RawFixture, TeamMetrics


logger = logging.getLogger(__name__)


class ApiFootballClient:
    """Клиент API-FOOTBALL / API-Sports.

    Важно:
    free plan API-FOOTBALL не даёт использовать параметр `/fixtures?team=...&last=...`.
    Поэтому при `APIFOOTBALL_FREE_PLAN=true` история команды берётся через:
    `/fixtures?team=...&season=...`
    и последние завершённые матчи режутся локально.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = f"https://{self.settings.apifootball_host}"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    async def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET-запрос к API-FOOTBALL."""
        headers = {
            "x-apisports-key": self.settings.apifootball_key,
            "x-rapidapi-host": self.settings.apifootball_host,
        }

        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(f"{self.base_url}{path}", params=params) as response:
                data = await response.json(content_type=None)

                if response.status >= 400:
                    raise RuntimeError(f"API-FOOTBALL HTTP {response.status} on {path}: {data}")

                errors = data.get("errors")
                if errors:
                    raise RuntimeError(f"API-FOOTBALL API errors on {path} with params={params}: {errors}")

                return data

    async def fixtures_by_date(self, target_date: date) -> list[RawFixture]:
        """Получить матчи на дату."""
        payload = await self.get_json(
            "/fixtures",
            {
                "date": target_date.isoformat(),
                "timezone": self.settings.tz,
            },
        )

        fixtures: list[RawFixture] = []
        for item in payload.get("response", []):
            fixture = item.get("fixture", {})
            league = item.get("league", {})
            teams = item.get("teams", {})

            fixtures.append(
                RawFixture(
                    fixture_id=str(fixture.get("id")),
                    date=fixture.get("date", ""),
                    timestamp=fixture.get("timestamp"),
                    status=fixture.get("status", {}),
                    league_id=str(league.get("id")),
                    league_name=league.get("name", ""),
                    country=league.get("country", ""),
                    season=league.get("season"),
                    home_team_id=str(teams.get("home", {}).get("id")),
                    away_team_id=str(teams.get("away", {}).get("id")),
                    home_team=teams.get("home", {}).get("name", ""),
                    away_team=teams.get("away", {}).get("name", ""),
                    provider="API_FOOTBALL",
                    source_league_code=str(league.get("id")),
                )
            )

        return fixtures

    async def fixture_by_id(self, fixture_id: str) -> dict[str, Any] | None:
        """Получить конкретный матч по ID."""
        payload = await self.get_json("/fixtures", {"id": fixture_id})
        response = payload.get("response", [])
        return response[0] if response else None

    async def team_last_fixtures(self, team_id: str, league_id: str | None = None, season: int | None = None, last: int = 8) -> list[dict[str, Any]]:
        """Последние завершённые матчи команды.

        На платном тарифе можно использовать `last`.
        На free plan используем `season`, потому что `last` запрещён.
        """
        if self.settings.apifootball_free_plan:
            params: dict[str, Any] = {
                "team": team_id,
                "season": season or self.settings.apifootball_season,
            }
            if league_id:
                params["league"] = league_id

            try:
                payload = await self.get_json("/fixtures", params)
                rows = payload.get("response", [])
            except Exception as exc:
                logger.warning("API_FOOTBALL free-plan team history failed with league, retry without league: %s", exc)
                payload = await self.get_json(
                    "/fixtures",
                    {
                        "team": team_id,
                        "season": season or self.settings.apifootball_season,
                    },
                )
                rows = payload.get("response", [])

            finished = [
                row for row in rows
                if row.get("fixture", {}).get("status", {}).get("short") in {"FT", "AET", "PEN"}
            ]
            finished.sort(key=lambda row: row.get("fixture", {}).get("timestamp") or 0, reverse=True)
            return finished[:last]

        payload = await self.get_json("/fixtures", {"team": team_id, "last": last})
        return payload.get("response", [])

    async def h2h(self, home_team_id: str, away_team_id: str, last: int = 5) -> list[dict[str, Any]]:
        """Очные матчи.

        Если free plan когда-нибудь ограничит h2h/last, просто возвращаем пусто,
        чтобы весь прогноз не падал.
        """
        try:
            payload = await self.get_json("/fixtures/headtohead", {"h2h": f"{home_team_id}-{away_team_id}", "last": last})
            return payload.get("response", [])
        except Exception as exc:
            logger.warning("API_FOOTBALL h2h skipped: %s", exc)
            return []

    async def standings(self, league_id: str, season: int | None) -> list[dict[str, Any]]:
        """Таблица лиги."""
        if not league_id or not season:
            return []

        try:
            payload = await self.get_json("/standings", {"league": league_id, "season": season})
            response = payload.get("response", [])
            if not response:
                return []
            return response[0].get("league", {}).get("standings", [[]])[0]
        except Exception as exc:
            logger.warning("API_FOOTBALL standings skipped: %s", exc)
            return []

    async def injuries(self, fixture_id: str) -> list[dict[str, Any]]:
        """Травмы по матчу.

        На free plan этот endpoint может быть ограничен, поэтому ошибка не должна ломать прогноз.
        """
        try:
            payload = await self.get_json("/injuries", {"fixture": fixture_id})
            return payload.get("response", [])
        except Exception as exc:
            logger.warning("API_FOOTBALL injuries skipped: %s", exc)
            return []

    async def odds(self, fixture_id: str) -> list[dict[str, Any]]:
        """Коэффициенты.

        На free plan odds часто недоступны. Ошибка не должна ломать прогноз.
        """
        try:
            payload = await self.get_json("/odds", {"fixture": fixture_id})
            return payload.get("response", [])
        except Exception as exc:
            logger.warning("API_FOOTBALL odds skipped: %s", exc)
            return []

    async def build_context(self, fixture: RawFixture) -> CandidateContext:
        """Собрать контекст матча."""
        logger.info(
            "API_FOOTBALL: собираю контекст %s — %s, league=%s, season=%s",
            fixture.home_team,
            fixture.away_team,
            fixture.league_id,
            fixture.season,
        )

        home_last, away_last, h2h_rows, standings_rows, injuries_rows, odds_rows = await asyncio.gather(
            self.team_last_fixtures(
                fixture.home_team_id,
                league_id=fixture.league_id,
                season=fixture.season or self.settings.apifootball_season,
                last=8,
            ),
            self.team_last_fixtures(
                fixture.away_team_id,
                league_id=fixture.league_id,
                season=fixture.season or self.settings.apifootball_season,
                last=8,
            ),
            self.h2h(fixture.home_team_id, fixture.away_team_id),
            self.standings(fixture.league_id, fixture.season or self.settings.apifootball_season),
            self.injuries(fixture.fixture_id),
            self.odds(fixture.fixture_id),
        )

        home_metrics = metrics_from_api_fixtures(home_last, fixture.home_team_id)
        away_metrics = metrics_from_api_fixtures(away_last, fixture.away_team_id)

        ctx = CandidateContext(
            fixture_id=fixture.fixture_id,
            provider=fixture.provider,
            source_league_code=fixture.source_league_code,
            start_time=fixture.date,
            country=fixture.country,
            league_id=fixture.league_id,
            league_name=fixture.league_name,
            home_team_id=fixture.home_team_id,
            away_team_id=fixture.away_team_id,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            home_metrics=home_metrics,
            away_metrics=away_metrics,
            h2h=compact_h2h(h2h_rows, fixture.home_team_id, fixture.away_team_id),
            standings=compact_standings(standings_rows, fixture.home_team_id, fixture.away_team_id),
            injuries=compact_injuries(injuries_rows),
            odds=simplify_odds(odds_rows),
            match_url="",
        )

        ctx.data_quality_score = calculate_data_quality(ctx)
        ctx.pre_ai_score = calculate_pre_ai_score(ctx)
        ctx.rejection_risks = detect_risks(ctx)
        return ctx


def metrics_from_api_fixtures(rows: list[dict[str, Any]], team_id: str) -> TeamMetrics:
    """Метрики команды из API-FOOTBALL fixtures."""
    team_name = ""

    for row in rows:
        teams = row.get("teams", {})
        if str(teams.get("home", {}).get("id")) == str(team_id):
            team_name = teams.get("home", {}).get("name") or ""
            break
        if str(teams.get("away", {}).get("id")) == str(team_id):
            team_name = teams.get("away", {}).get("name") or ""
            break

    metrics = TeamMetrics(team_id=str(team_id), name=team_name, matches=len(rows))

    for row in rows:
        teams = row.get("teams", {})
        goals = row.get("goals", {})

        is_home = str(teams.get("home", {}).get("id")) == str(team_id)

        gf = goals.get("home") if is_home else goals.get("away")
        ga = goals.get("away") if is_home else goals.get("home")

        if gf is None or ga is None:
            continue

        gf = int(gf)
        ga = int(ga)

        metrics.goals_for += gf
        metrics.goals_against += ga

        if gf > ga:
            metrics.wins += 1
        elif gf == ga:
            metrics.draws += 1
        else:
            metrics.losses += 1

        if gf + ga >= 2:
            metrics.over15 += 1
        if gf + ga >= 3:
            metrics.over25 += 1
        if gf >= 1 and ga >= 1:
            metrics.btts += 1
        if ga == 0:
            metrics.clean_sheets += 1

    return metrics


def compact_h2h(rows: list[dict[str, Any]], home_team_id: str, away_team_id: str) -> list[dict[str, Any]]:
    """Сжать H2H."""
    result = []

    for row in rows[:5]:
        teams = row.get("teams", {})
        goals = row.get("goals", {})
        result.append(
            {
                "date": row.get("fixture", {}).get("date"),
                "home": teams.get("home", {}).get("name"),
                "away": teams.get("away", {}).get("name"),
                "score": f"{goals.get('home')}:{goals.get('away')}",
            }
        )

    return result


def compact_standings(rows: list[dict[str, Any]], home_team_id: str, away_team_id: str) -> list[dict[str, Any]]:
    """Сжать таблицу только до двух команд."""
    result = []

    for row in rows:
        team = row.get("team", {})
        team_id = str(team.get("id"))
        if team_id not in {str(home_team_id), str(away_team_id)}:
            continue

        all_stats = row.get("all", {})
        result.append(
            {
                "rank": row.get("rank"),
                "team": team.get("name"),
                "points": row.get("points"),
                "goalsDiff": row.get("goalsDiff"),
                "played": all_stats.get("played"),
                "win": all_stats.get("win"),
                "draw": all_stats.get("draw"),
                "lose": all_stats.get("lose"),
            }
        )

    return result


def compact_injuries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сжать травмы."""
    result = []

    for row in rows[:10]:
        result.append(
            {
                "team": row.get("team", {}).get("name"),
                "player": row.get("player", {}).get("name"),
                "reason": row.get("player", {}).get("reason"),
            }
        )

    return result


def simplify_odds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сжать коэффициенты.

    Оставляем только рынки, которые похожи на тоталы/исходы.
    """
    result = []

    for item in rows[:2]:
        bookmakers = item.get("bookmakers", [])
        for bookmaker in bookmakers[:2]:
            for bet in bookmaker.get("bets", [])[:10]:
                name = str(bet.get("name", ""))
                if any(word in name.lower() for word in ["goals", "total", "over", "under", "match winner", "double chance"]):
                    result.append(
                        {
                            "bookmaker": bookmaker.get("name"),
                            "market": name,
                            "values": bet.get("values", [])[:8],
                        }
                    )

    return result[:10]


def calculate_data_quality(ctx: CandidateContext) -> int:
    """Оценка качества данных."""
    score = 0

    if ctx.home_metrics.matches >= 3:
        score += 20
    if ctx.away_metrics.matches >= 3:
        score += 20
    if ctx.h2h:
        score += 10
    if ctx.standings:
        score += 10
    if ctx.injuries:
        score += 5
    if ctx.odds:
        score += 5

    return min(100, score)


def calculate_pre_ai_score(ctx: CandidateContext) -> int:
    """Предварительный score."""
    h = ctx.home_metrics
    a = ctx.away_metrics

    if h.matches == 0 or a.matches == 0:
        return 0

    total_matches = max(1, h.matches + a.matches)
    over15_rate = (h.over15 + a.over15) / total_matches
    over25_rate = (h.over25 + a.over25) / total_matches
    btts_rate = (h.btts + a.btts) / total_matches

    score = 20
    score += int(over15_rate * 20)
    score += int(over25_rate * 15)
    score += int(btts_rate * 10)

    home_form = (h.wins * 3 + h.draws) / max(1, h.matches)
    away_form = (a.wins * 3 + a.draws) / max(1, a.matches)
    form_gap = abs(home_form - away_form)

    if form_gap >= 0.8:
        score += 15

    if ctx.data_quality_score >= 40:
        score += 10

    return min(100, score)


def detect_risks(ctx: CandidateContext) -> list[str]:
    """Риски прогноза."""
    risks: list[str] = []

    if ctx.home_metrics.matches < 3:
        risks.append(f"Мало последних матчей у {ctx.home_team}")
    if ctx.away_metrics.matches < 3:
        risks.append(f"Мало последних матчей у {ctx.away_team}")
    if not ctx.standings:
        risks.append("Нет таблицы лиги")
    if not ctx.odds:
        risks.append("Нет коэффициентов от источника")

    return risks



def detect_rejection_risks(ctx: CandidateContext, *args, **kwargs) -> list[str]:
    """Совместимость со старым LOCAL provider.

    LOCAL provider в старом коде может вызывать detect_rejection_risks(ctx, fixture)
    или detect_rejection_risks(ctx). Поэтому принимаем дополнительные аргументы
    и просто игнорируем их.
    """
    return detect_risks(ctx)
