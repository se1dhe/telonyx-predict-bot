from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.schemas import CandidateContext, TeamMetrics
from app.services.api_football import calculate_data_quality, calculate_pre_ai_score, detect_rejection_risks


LEAGUE_META: dict[str, dict[str, str]] = {
    "E0": {"name": "Premier League", "country": "England"},
    "E1": {"name": "Championship", "country": "England"},
    "E2": {"name": "League One", "country": "England"},
    "E3": {"name": "League Two", "country": "England"},
    "SP1": {"name": "La Liga", "country": "Spain"},
    "SP2": {"name": "La Liga 2", "country": "Spain"},
    "I1": {"name": "Serie A", "country": "Italy"},
    "I2": {"name": "Serie B", "country": "Italy"},
    "D1": {"name": "Bundesliga", "country": "Germany"},
    "D2": {"name": "2. Bundesliga", "country": "Germany"},
    "F1": {"name": "Ligue 1", "country": "France"},
    "F2": {"name": "Ligue 2", "country": "France"},
    "N1": {"name": "Eredivisie", "country": "Netherlands"},
    "P1": {"name": "Primeira Liga", "country": "Portugal"},
    "SC0": {"name": "Scottish Premiership", "country": "Scotland"},
}

THESPORTSDB_TO_LOCAL: dict[str, str] = {
    "4328": "E0",
    "4335": "SP1",
    "4332": "I1",
    "4331": "D1",
    "4334": "F1",
    "4337": "N1",
    "4344": "P1",
    "4330": "SC0",
}

ESPN_TO_LOCAL: dict[str, str] = {
    "eng.1": "E0",
    "esp.1": "SP1",
    "ita.1": "I1",
    "ger.1": "D1",
    "fra.1": "F1",
    "ned.1": "N1",
    "por.1": "P1",
    "sco.1": "SC0",
}


@dataclass
class LocalFixture:
    """Матч из бесплатного источника."""

    fixture_id: str
    league_code: str
    date: date
    time: str
    home_team: str
    away_team: str
    source: str = "unknown"


class FreeDataProvider:
    """Бесплатный источник данных.

    Расписание:
    1. football-data.co.uk fixtures.csv
    2. TheSportsDB eventsnextleague fallback
    3. ESPN scoreboard fallback

    Аналитика:
    - football-data.co.uk historical CSV
    - ClubElo
    - SerpAPI новости на уровне pipeline
    """

    FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
    HISTORICAL_BASE = "https://www.football-data.co.uk/mmz4281"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._history_cache: dict[str, list[dict[str, str]]] = {}
        self._elo_cache: dict[str, int] = {}
        self.last_debug: dict[str, Any] = {}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        reraise=True,
    )
    async def fetch_text(self, url: str) -> str:
        """Скачать текстовый файл."""
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"User-Agent": "Mozilla/5.0 FootballGoldBot/1.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                text = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"LOCAL HTTP {response.status} for {url}: {text[:300]}")
                return text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        reraise=True,
    )
    async def fetch_json(self, url: str) -> dict[str, Any]:
        """Скачать JSON."""
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"User-Agent": "Mozilla/5.0 FootballGoldBot/1.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                text = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"LOCAL JSON HTTP {response.status} for {url}: {text[:300]}")
                return await response.json(content_type=None)

    async def fixtures_by_date(self, target_date: date) -> list[LocalFixture]:
        """Получить матчи на дату и lookahead."""
        self.last_debug = {
            "football_data_fixtures": 0,
            "thesportsdb_fixtures": 0,
            "espn_fixtures": 0,
            "used_source": "",
            "fallback_errors": [],
        }

        fixtures = await self._fixtures_from_football_data(target_date)
        self.last_debug["football_data_fixtures"] = len(fixtures)

        if fixtures:
            self.last_debug["used_source"] = "football-data.co.uk"
            return deduplicate_fixtures(sorted(fixtures, key=fixture_sort_key))

        if self.settings.thesportsdb_enabled:
            try:
                fixtures = await self._fixtures_from_thesportsdb(target_date)
                self.last_debug["thesportsdb_fixtures"] = len(fixtures)
            except Exception as exc:
                self.last_debug["fallback_errors"].append(f"TheSportsDB: {str(exc)[:180]}")
                fixtures = []

        if fixtures:
            self.last_debug["used_source"] = "thesportsdb.com"
            return deduplicate_fixtures(sorted(fixtures, key=fixture_sort_key))

        if self.settings.espn_enabled:
            try:
                fixtures = await self._fixtures_from_espn(target_date)
                self.last_debug["espn_fixtures"] = len(fixtures)
            except Exception as exc:
                self.last_debug["fallback_errors"].append(f"ESPN: {str(exc)[:180]}")
                fixtures = []

        if fixtures:
            self.last_debug["used_source"] = "ESPN scoreboard"
            return deduplicate_fixtures(sorted(fixtures, key=fixture_sort_key))

        self.last_debug["used_source"] = "none"
        return []

    async def _fixtures_from_football_data(self, target_date: date) -> list[LocalFixture]:
        """Матчи из football-data.co.uk fixtures.csv."""
        text = await self.fetch_text(self.FIXTURES_URL)
        rows = list(csv.DictReader(io.StringIO(text)))

        league_codes = set(self.settings.local_league_codes)
        allowed_countries = self.settings.allowed_countries
        end_date = target_date + timedelta(days=max(0, self.settings.local_lookahead_days - 1))

        fixtures: list[LocalFixture] = []
        for row in rows:
            div = (row.get("Div") or "").strip().upper()
            if league_codes and div not in league_codes:
                continue

            meta = LEAGUE_META.get(div, {"name": div, "country": ""})
            if allowed_countries and meta["country"].lower() not in allowed_countries:
                continue

            parsed_date = parse_football_data_date(row.get("Date") or "")
            if not parsed_date:
                continue
            if not (target_date <= parsed_date <= end_date):
                continue

            home = clean_team_name(row.get("HomeTeam") or "")
            away = clean_team_name(row.get("AwayTeam") or "")
            if not home or not away:
                continue

            fixtures.append(
                LocalFixture(
                    fixture_id=f"LOCAL:FD:{div}:{parsed_date.isoformat()}:{slugify(home)}:{slugify(away)}",
                    league_code=div,
                    date=parsed_date,
                    time=(row.get("Time") or "").strip(),
                    home_team=home,
                    away_team=away,
                    source="football-data.co.uk",
                )
            )

        return fixtures

    async def _fixtures_from_thesportsdb(self, target_date: date) -> list[LocalFixture]:
        """Fallback-матчи из TheSportsDB."""
        league_ids = self.settings.thesportsdb_league_ids
        if not league_ids:
            return []

        league_codes = set(self.settings.local_league_codes)
        allowed_countries = self.settings.allowed_countries
        end_date = target_date + timedelta(days=max(0, self.settings.local_lookahead_days - 1))

        result: list[LocalFixture] = []

        for league_id in league_ids:
            league_code = THESPORTSDB_TO_LOCAL.get(league_id)
            if not league_code:
                continue

            if league_codes and league_code not in league_codes:
                continue

            meta = LEAGUE_META.get(league_code, {"name": league_code, "country": ""})
            if allowed_countries and meta["country"].lower() not in allowed_countries:
                continue

            url = (
                f"https://www.thesportsdb.com/api/v1/json/"
                f"{self.settings.thesportsdb_api_key}/eventsnextleague.php?id={league_id}"
            )

            data = await self.fetch_json(url)
            events = data.get("events") or []

            for event in events:
                parsed_date = parse_thesportsdb_date(event.get("dateEvent") or "")
                if not parsed_date:
                    continue

                if not (target_date <= parsed_date <= end_date):
                    continue

                home = clean_team_name(event.get("strHomeTeam") or "")
                away = clean_team_name(event.get("strAwayTeam") or "")
                if not home or not away:
                    continue

                result.append(
                    LocalFixture(
                        fixture_id=f"LOCAL:TSD:{event.get('idEvent') or ''}:{league_code}:{parsed_date.isoformat()}:{slugify(home)}:{slugify(away)}",
                        league_code=league_code,
                        date=parsed_date,
                        time=(event.get("strTime") or "").strip(),
                        home_team=home,
                        away_team=away,
                        source="thesportsdb.com",
                    )
                )

        return result

    async def _fixtures_from_espn(self, target_date: date) -> list[LocalFixture]:
        """Fallback-матчи из ESPN scoreboard.

        ESPN не требует ключа для site scoreboard.
        """
        league_codes = set(self.settings.local_league_codes)
        allowed_countries = self.settings.allowed_countries
        end_date = target_date + timedelta(days=max(0, self.settings.local_lookahead_days - 1))

        result: list[LocalFixture] = []
        dates_param = target_date.strftime("%Y%m%d")

        for espn_league in self.settings.espn_leagues:
            league_code = ESPN_TO_LOCAL.get(espn_league)
            if not league_code:
                continue

            if league_codes and league_code not in league_codes:
                continue

            meta = LEAGUE_META.get(league_code, {"name": league_code, "country": ""})
            if allowed_countries and meta["country"].lower() not in allowed_countries:
                continue

            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{espn_league}/scoreboard?dates={dates_param}"
            data = await self.fetch_json(url)

            for event in data.get("events", []) or []:
                parsed_date, parsed_time = parse_espn_event_datetime(event.get("date") or "")
                if not parsed_date:
                    continue

                if not (target_date <= parsed_date <= end_date):
                    continue

                home = ""
                away = ""

                competitions = event.get("competitions") or []
                if competitions:
                    competitors = competitions[0].get("competitors") or []
                    for comp in competitors:
                        team_name = (comp.get("team") or {}).get("displayName") or (comp.get("team") or {}).get("shortDisplayName") or ""
                        if comp.get("homeAway") == "home":
                            home = clean_team_name(team_name)
                        elif comp.get("homeAway") == "away":
                            away = clean_team_name(team_name)

                if not home or not away:
                    name = event.get("name") or ""
                    if " at " in name:
                        away, home = [clean_team_name(x) for x in name.split(" at ", 1)]
                    elif " vs " in name:
                        home, away = [clean_team_name(x) for x in name.split(" vs ", 1)]

                if not home or not away:
                    continue

                result.append(
                    LocalFixture(
                        fixture_id=f"LOCAL:ESPN:{event.get('id') or ''}:{league_code}:{parsed_date.isoformat()}:{slugify(home)}:{slugify(away)}",
                        league_code=league_code,
                        date=parsed_date,
                        time=parsed_time,
                        home_team=home,
                        away_team=away,
                        source="ESPN scoreboard",
                    )
                )

        return result

    async def build_context(self, fixture: LocalFixture) -> CandidateContext:
        """Собрать контекст матча из бесплатных данных."""
        history = await self.history_for_league(fixture.league_code, fixture.date)
        elo_map = await self.clubelo_for_date(fixture.date) if self.settings.clubelo_enabled else {}

        home_metrics = build_local_team_metrics(fixture.home_team, history)
        away_metrics = build_local_team_metrics(fixture.away_team, history)

        home_metrics.elo = find_elo(elo_map, fixture.home_team)
        away_metrics.elo = find_elo(elo_map, fixture.away_team)

        h2h = build_local_h2h(fixture.home_team, fixture.away_team, history)
        standings = build_local_standings(history)
        odds = build_local_odds(fixture.home_team, fixture.away_team, history)

        meta = LEAGUE_META.get(fixture.league_code, {"name": fixture.league_code, "country": ""})

        start_time = fixture.date.isoformat()
        if fixture.time:
            start_time = f"{fixture.date.isoformat()} {fixture.time}"

        ctx = CandidateContext(
            provider="LOCAL",
            fixture_id=fixture.fixture_id,
            source_league_code=fixture.league_code,
            start_time=start_time,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            home_team_id=slugify(fixture.home_team),
            away_team_id=slugify(fixture.away_team),
            league_id=fixture.league_code,
            league_name=meta["name"],
            country=meta["country"],
            venue=None,
            home_metrics=home_metrics,
            away_metrics=away_metrics,
            h2h=h2h,
            standings=standings,
            injuries=[],
            odds=odds,
        )

        ctx.data_quality_score = calculate_data_quality(ctx)
        ctx.pre_ai_score = calculate_pre_ai_score(ctx)
        ctx.rejection_risks = detect_rejection_risks(ctx, self.settings.local_min_form_matches)
        return ctx

    async def history_for_league(self, league_code: str, target_date: date) -> list[dict[str, str]]:
        """История текущего сезона по лиге."""
        season = football_data_season_code(target_date)
        cache_key = f"{season}:{league_code}"

        if cache_key in self._history_cache:
            return self._history_cache[cache_key]

        url = f"{self.HISTORICAL_BASE}/{season}/{league_code}.csv"
        text = await self.fetch_text(url)
        rows = list(csv.DictReader(io.StringIO(text)))

        played: list[dict[str, str]] = []
        for row in rows:
            if not row.get("FTHG") or not row.get("FTAG"):
                continue
            played.append(row)

        self._history_cache[cache_key] = played
        return played

    async def clubelo_for_date(self, target_date: date) -> dict[str, int]:
        """Рейтинги ClubElo на дату."""
        if self._elo_cache:
            return self._elo_cache

        url = f"https://api.clubelo.com/{target_date.isoformat()}"
        try:
            text = await self.fetch_text(url)
        except Exception:
            self._elo_cache = {}
            return {}

        rows = list(csv.DictReader(io.StringIO(text)))
        result: dict[str, int] = {}
        for row in rows:
            club = row.get("Club") or ""
            elo = row.get("Elo") or ""
            if not club or not elo:
                continue
            try:
                result[normalize_team_key(club)] = int(float(elo))
            except ValueError:
                continue

        self._elo_cache = result
        return result

    async def result_for_prediction(self, fixture_id: str, league_code: str, home_team: str, away_team: str, target_date: date) -> tuple[int, int] | None:
        """Найти финальный счёт для LOCAL прогноза."""
        history = await self.history_for_league(league_code, target_date)
        for row in history:
            row_date = parse_football_data_date(row.get("Date") or "")
            if row_date != target_date:
                continue

            if same_team(row.get("HomeTeam") or "", home_team) and same_team(row.get("AwayTeam") or "", away_team):
                try:
                    return int(row["FTHG"]), int(row["FTAG"])
                except Exception:
                    return None

        return None


def fixture_sort_key(fixture: LocalFixture) -> tuple[str, str, str, str]:
    """Ключ сортировки матчей."""
    return (fixture.date.isoformat(), fixture.time, fixture.league_code, fixture.home_team)


def football_data_season_code(target_date: date) -> str:
    """Код сезона football-data.co.uk."""
    if target_date.month >= 7:
        start_year = target_date.year
        end_year = target_date.year + 1
    else:
        start_year = target_date.year - 1
        end_year = target_date.year

    return f"{start_year % 100:02d}{end_year % 100:02d}"


def parse_football_data_date(value: str) -> date | None:
    """Разобрать дату football-data.co.uk."""
    value = value.strip()
    if not value:
        return None

    value = value.split()[0]

    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def parse_thesportsdb_date(value: str) -> date | None:
    """Разобрать дату TheSportsDB."""
    value = value.strip()
    if not value:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def parse_espn_event_datetime(value: str) -> tuple[date | None, str]:
    """Разобрать дату события ESPN."""
    value = value.strip()
    if not value:
        return None, ""

    try:
        # Пример: 2026-05-03T15:30Z или 2026-05-03T15:30:00Z
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.date(), dt.strftime("%H:%M")
    except ValueError:
        pass

    try:
        dt = datetime.strptime(value[:10], "%Y-%m-%d")
        return dt.date(), ""
    except ValueError:
        return None, ""


def build_local_team_metrics(team_name: str, history: list[dict[str, str]], limit: int = 8) -> TeamMetrics:
    """Посчитать форму команды из CSV истории."""
    metrics = TeamMetrics(team_id=slugify(team_name), name=team_name)
    matches = []

    for row in history:
        home = clean_team_name(row.get("HomeTeam") or "")
        away = clean_team_name(row.get("AwayTeam") or "")

        if not same_team(home, team_name) and not same_team(away, team_name):
            continue

        try:
            home_goals = int(row.get("FTHG") or "")
            away_goals = int(row.get("FTAG") or "")
        except ValueError:
            continue

        row_date = parse_football_data_date(row.get("Date") or "")
        matches.append((row_date or date.min, home, away, home_goals, away_goals))

    matches.sort(key=lambda x: x[0], reverse=True)

    for _, home, away, home_goals, away_goals in matches[:limit]:
        is_home = same_team(home, team_name)
        gf = home_goals if is_home else away_goals
        ga = away_goals if is_home else home_goals
        opponent = away if is_home else home
        apply_match_to_metrics(metrics, gf, ga, opponent)

    return metrics


def apply_match_to_metrics(metrics: TeamMetrics, gf: int, ga: int, opponent: str) -> None:
    """Добавить матч в метрики."""
    metrics.matches += 1
    metrics.goals_for += gf
    metrics.goals_against += ga

    if gf > ga:
        metrics.wins += 1
        mark = "W"
    elif gf == ga:
        metrics.draws += 1
        mark = "D"
    else:
        metrics.losses += 1
        mark = "L"

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


def build_local_h2h(home_team: str, away_team: str, history: list[dict[str, str]], limit: int = 5) -> list[dict[str, Any]]:
    """Очные встречи из истории лиги."""
    rows = []

    for row in history:
        home = clean_team_name(row.get("HomeTeam") or "")
        away = clean_team_name(row.get("AwayTeam") or "")

        direct = same_team(home, home_team) and same_team(away, away_team)
        reverse = same_team(home, away_team) and same_team(away, home_team)
        if not direct and not reverse:
            continue

        try:
            home_goals = int(row.get("FTHG") or "")
            away_goals = int(row.get("FTAG") or "")
        except ValueError:
            continue

        row_date = parse_football_data_date(row.get("Date") or "")
        rows.append(
            {
                "date": row_date.isoformat() if row_date else None,
                "home": home,
                "away": away,
                "home_goals": home_goals,
                "away_goals": away_goals,
            }
        )

    rows.sort(key=lambda x: x.get("date") or "", reverse=True)
    return rows[:limit]


def build_local_standings(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Примерная таблица по сыгранным матчам текущего сезона."""
    table: dict[str, dict[str, Any]] = {}

    def ensure(team: str) -> dict[str, Any]:
        key = normalize_team_key(team)
        if key not in table:
            table[key] = {
                "team": team,
                "played": 0,
                "win": 0,
                "draw": 0,
                "lose": 0,
                "goals_for": 0,
                "goals_against": 0,
                "points": 0,
            }
        return table[key]

    for row in history:
        home = clean_team_name(row.get("HomeTeam") or "")
        away = clean_team_name(row.get("AwayTeam") or "")
        try:
            hg = int(row.get("FTHG") or "")
            ag = int(row.get("FTAG") or "")
        except ValueError:
            continue

        h = ensure(home)
        a = ensure(away)

        h["played"] += 1
        a["played"] += 1
        h["goals_for"] += hg
        h["goals_against"] += ag
        a["goals_for"] += ag
        a["goals_against"] += hg

        if hg > ag:
            h["win"] += 1
            a["lose"] += 1
            h["points"] += 3
        elif hg < ag:
            a["win"] += 1
            h["lose"] += 1
            a["points"] += 3
        else:
            h["draw"] += 1
            a["draw"] += 1
            h["points"] += 1
            a["points"] += 1

    rows = list(table.values())
    rows.sort(key=lambda x: (x["points"], x["goals_for"] - x["goals_against"], x["goals_for"]), reverse=True)

    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx

    return rows


def build_local_odds(home_team: str, away_team: str, history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Исторические odds по последним матчам команд."""
    odds_rows = []
    interesting_cols = ["B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA", "MaxH", "MaxD", "MaxA"]

    for row in reversed(history):
        home = clean_team_name(row.get("HomeTeam") or "")
        away = clean_team_name(row.get("AwayTeam") or "")
        if not (same_team(home, home_team) or same_team(away, home_team) or same_team(home, away_team) or same_team(away, away_team)):
            continue

        odds = {col: row.get(col) for col in interesting_cols if row.get(col)}
        if odds:
            odds_rows.append(
                {
                    "date": row.get("Date"),
                    "home": home,
                    "away": away,
                    "odds": odds,
                }
            )

        if len(odds_rows) >= 8:
            break

    return [{"bookmaker": "football-data.co.uk historical odds", "bets": odds_rows}] if odds_rows else []


def find_elo(elo_map: dict[str, int], team_name: str) -> int | None:
    """Найти Elo по названию команды."""
    if not elo_map:
        return None

    key = normalize_team_key(team_name)
    if key in elo_map:
        return elo_map[key]

    for elo_key, value in elo_map.items():
        if key in elo_key or elo_key in key:
            return value

    return None


def deduplicate_fixtures(fixtures: list[LocalFixture]) -> list[LocalFixture]:
    """Убрать дубли матчей."""
    seen: set[tuple[str, str, str]] = set()
    result: list[LocalFixture] = []

    for fixture in fixtures:
        key = (
            fixture.date.isoformat(),
            normalize_team_key(fixture.home_team),
            normalize_team_key(fixture.away_team),
        )
        if key in seen:
            continue

        seen.add(key)
        result.append(fixture)

    return result


def clean_team_name(value: str) -> str:
    return value.strip()


def normalize_team_key(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"\b(fc|afc|cf|sc|club)\b", "", value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def same_team(left: str, right: str) -> bool:
    return normalize_team_key(left) == normalize_team_key(right)
