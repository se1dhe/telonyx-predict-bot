from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.schemas import CandidateContext, RawFixture, TeamMetrics
from app.services.ggbet_scraper import GGBetEvent, GGBetScraper, ggbet_event_to_odds, teams_match


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
        if self.settings.ggbet_odds_first_enabled:
            return await self.fixtures_by_ggbet_odds_date(target_date)

        if self.settings.odds_first_enabled:
            return await self.fixtures_by_odds_date(target_date)

        return await self.fixtures_by_regular_date(target_date)

    async def fixtures_by_regular_date(self, target_date: date) -> list[RawFixture]:
        """Получить все fixtures API-FOOTBALL на дату без odds-first."""
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

    async def fixtures_by_ggbet_odds_date(self, target_date: date) -> list[RawFixture]:
        """Use GGBET as the odds/link source, then attach API-Football fixture ids for stats."""
        ggbet_events = await GGBetScraper().events_by_date(target_date)
        if not ggbet_events:
            logger.warning("GGBET odds-first returned no events for %s", target_date)
            return []

        api_fixtures = await self.fixtures_by_regular_date(target_date)
        fixtures: list[RawFixture] = []
        used_fixture_ids: set[str] = set()

        for event in ggbet_events:
            fixture = match_ggbet_event_to_fixture(event, api_fixtures, used_fixture_ids)
            if not fixture:
                logger.info("GGBET odds-first skipped unmatched event: %s", event.title)
                continue

            used_fixture_ids.add(fixture.fixture_id)
            fixtures.append(
                fixture.model_copy(
                    update={
                        "provider": "API_FOOTBALL",
                        "prematch_odds": ggbet_event_to_odds(event),
                        "match_url": event.url,
                    }
                )
            )

            if len(fixtures) >= max(1, self.settings.max_raw_events):
                break

        logger.info(
            "GGBET odds-first returned fixtures=%s from ggbet_events=%s api_fixtures=%s",
            len(fixtures),
            len(ggbet_events),
            len(api_fixtures),
        )
        return fixtures

    async def fixtures_by_odds_date(self, target_date: date) -> list[RawFixture]:
        """Получить матчдэй из pre-match odds, а не из общего расписания."""
        rows = await self.odds_by_date(target_date)
        fixtures: list[RawFixture] = []
        seen: set[str] = set()

        for row in rows:
            if not odds_row_has_min_allowed_market(row, self.settings.min_pick_odds, set(self.settings.safe_mode_allowed_bets)):
                continue

            fixture_obj = row.get("fixture", {}) or {}
            fixture_id = str(fixture_obj.get("id") or "")
            if not fixture_id or fixture_id in seen:
                continue

            details = await self.fixture_by_id(fixture_id)
            if not details:
                logger.info("API_FOOTBALL odds-first skipped fixture=%s: fixture details not found", fixture_id)
                continue

            fixture = details.get("fixture", {}) or {}
            league = details.get("league", {}) or row.get("league", {}) or {}
            teams = details.get("teams", {}) or {}

            home = teams.get("home", {}) or {}
            away = teams.get("away", {}) or {}
            home_name = str(home.get("name") or "")
            away_name = str(away.get("name") or "")
            if not home_name or not away_name:
                logger.info("API_FOOTBALL odds-first skipped fixture=%s: team names missing", fixture_id)
                continue

            seen.add(fixture_id)
            fixtures.append(
                RawFixture(
                    fixture_id=fixture_id,
                    date=fixture.get("date") or fixture_obj.get("date") or "",
                    timestamp=fixture.get("timestamp") or fixture_obj.get("timestamp"),
                    status=fixture.get("status") or {"short": "NS"},
                    league_id=str(league.get("id") or ""),
                    league_name=str(league.get("name") or ""),
                    country=str(league.get("country") or ""),
                    season=league.get("season"),
                    home_team_id=str(home.get("id") or ""),
                    away_team_id=str(away.get("id") or ""),
                    home_team=home_name,
                    away_team=away_name,
                    provider="API_FOOTBALL",
                    source_league_code=str(league.get("id") or ""),
                    prematch_odds=simplify_odds([row]),
                )
            )
            if len(fixtures) >= max(1, self.settings.max_raw_events):
                break

        logger.info("API_FOOTBALL odds-first returned fixtures=%s from odds_rows=%s", len(fixtures), len(rows))
        return fixtures

    async def odds_by_date(self, target_date: date) -> list[dict[str, Any]]:
        """Получить pre-match odds на дату с учётом пагинации API-Football."""
        params: dict[str, Any] = {
            "date": target_date.isoformat(),
        }
        if self.settings.odds_first_bookmaker_id.strip():
            params["bookmaker"] = self.settings.odds_first_bookmaker_id.strip()

        bet_ids = self.settings.odds_first_bet_ids
        if bet_ids:
            rows: list[dict[str, Any]] = []
            for bet_id in bet_ids:
                bet_params = dict(params)
                bet_params["bet"] = bet_id
                rows.extend(await self._paged_odds(bet_params))
            return rows

        return await self._paged_odds(params)

    async def _paged_odds(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1

        while True:
            payload = await self.get_json("/odds", {**params, "page": page})
            rows.extend(payload.get("response", []) or [])

            paging = payload.get("paging") or {}
            current = int(paging.get("current") or page)
            total = int(paging.get("total") or current)
            if current >= total:
                break

            page += 1

        return rows

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

        odds_coro = self.odds(fixture.fixture_id) if not fixture.prematch_odds else asyncio.sleep(0, result=fixture.prematch_odds)

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
            odds_coro,
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
            odds=odds_rows if fixture.prematch_odds else simplify_odds(odds_rows),
            match_url=fixture.match_url,
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


def match_ggbet_event_to_fixture(
    event: GGBetEvent,
    fixtures: list[RawFixture],
    used_fixture_ids: set[str],
) -> RawFixture | None:
    """Find the API-Football fixture for a GGBET event by team names."""
    best: tuple[int, RawFixture] | None = None
    for fixture in fixtures:
        if fixture.fixture_id in used_fixture_ids:
            continue

        direct = teams_match(event.home_team, fixture.home_team) and teams_match(event.away_team, fixture.away_team)
        swapped = teams_match(event.home_team, fixture.away_team) and teams_match(event.away_team, fixture.home_team)
        if not direct and not swapped:
            continue

        score = 100 if direct else 80
        if event.start_time and fixture.timestamp:
            # Prefer the fixture closest to the GGBET start time when duplicated names exist.
            try:
                delta_minutes = abs(int(event.start_time.timestamp()) - int(fixture.timestamp)) // 60
                score -= min(30, int(delta_minutes))
            except Exception:
                pass

        if best is None or score > best[0]:
            best = (score, fixture)

    return best[1] if best else None


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


def odds_row_has_min_allowed_market(row: dict[str, Any], min_odds: float, allowed_bets: set[str]) -> bool:
    """Проверить, что в строке odds есть разрешённый рынок с нужным коэффициентом."""
    for bookmaker in row.get("bookmakers", []) or []:
        for bet in bookmaker.get("bets", []) or []:
            market = str(bet.get("name") or "")
            for value in bet.get("values", []) or []:
                if not isinstance(value, dict):
                    continue
                odd = parse_odd(value.get("odd"))
                if odd is None or odd < min_odds:
                    continue
                if bet_value_to_code(market, str(value.get("value") or "")) in allowed_bets:
                    return True

    return False


def bet_value_to_code(market: str, value: str) -> str:
    """Грубо сопоставить рынок API-Football с внутренним BetCode."""
    market_l = str(market or "").lower()
    value_l = str(value or "").lower()
    text = f"{market_l} {value_l}"

    if "over" in text and ("1.5" in text or "1,5" in text):
        return "OVER_1_5"
    if "over" in text and ("2.5" in text or "2,5" in text):
        return "OVER_2_5"
    if ("both teams" in market_l or "btts" in market_l) and value_l in {"yes", "так"}:
        return "BTTS_YES"
    if "double chance" in market_l and ("home/draw" in value_l or "1x" in value_l):
        return "HOME_DOUBLE_CHANCE"
    if "double chance" in market_l and ("draw/away" in value_l or "x2" in value_l):
        return "AWAY_DOUBLE_CHANCE"
    if ("draw no bet" in market_l or "dnb" in market_l) and value_l == "home":
        return "HOME_DNB"
    if ("draw no bet" in market_l or "dnb" in market_l) and value_l == "away":
        return "AWAY_DNB"

    return ""


def parse_odd(raw: object) -> float | None:
    try:
        odd = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return odd if odd > 1 else None


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
