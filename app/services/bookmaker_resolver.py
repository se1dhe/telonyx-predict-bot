from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


class SerpApiRateLimited(RuntimeError):
    """SerpAPI вернул 429: дальше искать нельзя, чтобы не сжигать лимит."""


@dataclass(frozen=True)
class BookmakerProvider:
    """Описание источника, где ищем точную страницу матча."""

    key: str
    display_name: str
    domains: tuple[str, ...]
    queries: tuple[str, ...]


PROVIDERS: dict[str, BookmakerProvider] = {
    "pinnacle": BookmakerProvider(
        key="pinnacle",
        display_name="Pinnacle",
        domains=("pinnacle.com",),
        queries=(
            'site:pinnacle.com/ru/soccer "{home}" "{away}"',
            'site:pinnacle.com/en/soccer "{home}" "{away}"',
            'site:pinnacle.com "{home}" "{away}" soccer odds',
        ),
    ),
    "oddschecker": BookmakerProvider(
        key="oddschecker",
        display_name="Oddschecker",
        domains=("oddschecker.com",),
        queries=(
            'site:oddschecker.com/football "{home}" "{away}"',
            'site:oddschecker.com/us/soccer "{home}" "{away}"',
            'site:oddschecker.com "{home} v {away}" football odds',
        ),
    ),
    "unibet": BookmakerProvider(
        key="unibet",
        display_name="Unibet",
        domains=("unibet.com", "unibet.co.uk"),
        queries=(
            'site:unibet.com/betting/sports/filter/football "{home}" "{away}"',
            'site:unibet.com "{home}" "{away}" football odds',
            'site:unibet.co.uk "{home}" "{away}" football odds',
        ),
    ),
    "ggbet": BookmakerProvider(
        key="ggbet",
        display_name="GGBET",
        domains=("ggbet.ua", "gg.bet"),
        queries=(
            'site:ggbet.ua "{home}" "{away}" футбол',
            'site:ggbet.ua/uk-ua/bets "{home}" "{away}"',
        ),
    ),
    "betking": BookmakerProvider(
        key="betking",
        display_name="BetKing",
        domains=("betking.com.ua",),
        queries=(
            'site:betking.com.ua/sports-book "{home}" "{away}"',
            'site:betking.com.ua "{home}" "{away}" футбол',
        ),
    ),
}


class BookmakerResolver:
    """Поиск точной букмекерской страницы матча.

    Работает экономно: при первом 429 от SerpAPI прекращает поиск по матчу,
    чтобы не делать ещё 5-10 бесполезных запросов.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._cache: dict[str, tuple[str, str]] = {}
        self._ggbet = GGBetResolver()

    async def resolve(self, home_team: str, away_team: str, start_time: str = "") -> tuple[str, str]:
        """Вернуть tuple(url, provider_name) или ("", "")."""
        if not self.settings.bookmaker_link_enabled:
            return "", ""

        home_team = str(home_team or "").strip()
        away_team = str(away_team or "").strip()
        if not home_team or not away_team:
            return "", ""

        provider_keys = self._provider_order()
        if provider_keys and provider_keys[0] == "ggbet":
            provider_keys = ["ggbet"]

        if not self.settings.serpapi_key and provider_keys != ["ggbet"]:
            logger.warning("Bookmaker resolver skipped: SERPAPI_KEY is empty")
            return "", ""

        cache_key = f"{normalize_text(home_team)}::{normalize_text(away_team)}::{start_time if provider_keys == ['ggbet'] else ''}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result = await asyncio.wait_for(
                self._resolve_inner(home_team, away_team, provider_keys, start_time),
                timeout=max(3, self.settings.news_timeout_seconds),
            )
        except SerpApiRateLimited:
            logger.warning("Bookmaker resolver stopped after SerpAPI 429 for %s — %s", home_team, away_team)
            result = ("", "")
        except Exception as exc:
            logger.warning("Bookmaker resolver failed for %s — %s: %s", home_team, away_team, exc)
            result = ("", "")

        self._cache[cache_key] = result
        return result

    def _provider_order(self) -> list[str]:
        """Собрать порядок провайдеров: primary + fallback без дублей."""
        result: list[str] = []
        primary = (getattr(self.settings, "bookmaker_resolver_provider", "pinnacle") or "pinnacle").strip().lower()
        if primary:
            result.append(primary)

        fallback_raw = getattr(self.settings, "bookmaker_fallback_providers_raw", "oddschecker,ggbet,betking")
        for provider in [x.strip().lower() for x in fallback_raw.split(",") if x.strip()]:
            if provider not in result:
                result.append(provider)

        return [provider for provider in result if provider in PROVIDERS]

    async def _resolve_inner(self, home_team: str, away_team: str, provider_keys: list[str], start_time: str = "") -> tuple[str, str]:
        for provider_key in provider_keys:
            if provider_key == "ggbet":
                url = await self._ggbet.resolve(home_team, away_team, start_time)
                if url:
                    logger.info("GGBET exact URL resolved for %s — %s: %s", home_team, away_team, url)
                    return url, "GGBET"
                continue

            provider = PROVIDERS[provider_key]
            url = await self._search_provider(provider, home_team, away_team)
            if url:
                logger.info(
                    "Bookmaker exact URL resolved: provider=%s match=%s — %s url=%s",
                    provider.display_name,
                    home_team,
                    away_team,
                    url,
                )
                return url, provider.display_name

        logger.info("Bookmaker exact URL not found for %s — %s", home_team, away_team)
        return "", ""

    async def _search_provider(self, provider: BookmakerProvider, home_team: str, away_team: str) -> str:
        for query_template in provider.queries:
            query = query_template.format(home=home_team, away=away_team)
            url = await self._search_one(provider, query, home_team, away_team)
            if url:
                return url
        return ""

    async def _search_one(self, provider: BookmakerProvider, query: str, home_team: str, away_team: str) -> str:
        params = {
            "engine": "google",
            "q": query,
            "api_key": self.settings.serpapi_key,
            "num": max(1, int(getattr(self.settings, "bookmaker_resolver_max_results", 3) or 3)),
            "hl": "en",
        }

        timeout = aiohttp.ClientTimeout(total=max(3, self.settings.news_timeout_seconds))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://serpapi.com/search.json", params=params) as response:
                if response.status == 429:
                    logger.warning("SerpAPI bookmaker search HTTP 429 provider=%s query=%s", provider.key, query)
                    raise SerpApiRateLimited()
                if response.status >= 400:
                    logger.warning(
                        "SerpAPI bookmaker search HTTP %s provider=%s query=%s",
                        response.status,
                        provider.key,
                        query,
                    )
                    return ""
                data = await response.json(content_type=None)

        candidates = extract_candidates(data)
        home_norm = normalize_text(home_team)
        away_norm = normalize_text(away_team)

        for candidate in candidates:
            link = clean_url(candidate.get("link", ""), provider.domains)
            if not link:
                continue

            haystack = normalize_text(
                " ".join(
                    [
                        candidate.get("title", ""),
                        candidate.get("snippet", ""),
                        urlparse(link).path,
                    ]
                )
            )

            if teams_match(haystack, home_norm, away_norm):
                return link

        return ""


class GGBetResolver:
    """Точный resolver GGBET через betting GraphQL, а не угадывание slug."""

    bootstrap_paths = ("https://ggbet.ua/{locale}", "https://ggbet.ua/{locale}/sports")

    def __init__(self) -> None:
        self.settings = get_settings()
        self._bootstrap: dict[str, str] | None = None
        self._cache: dict[str, str] = {}

    async def resolve(self, home_team: str, away_team: str, start_time: str = "") -> str:
        home_team = str(home_team or "").strip()
        away_team = str(away_team or "").strip()
        if not home_team or not away_team:
            return ""

        cache_key = "::".join([normalize_text(home_team), normalize_text(away_team), str(start_time or "")])
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result = await asyncio.wait_for(
                self._resolve_inner(home_team, away_team, start_time),
                timeout=max(8, int(getattr(self.settings, "news_timeout_seconds", 4) or 4)),
            )
        except Exception as exc:
            logger.warning("GGBET resolver failed for %s — %s: %s", home_team, away_team, exc)
            result = ""

        self._cache[cache_key] = result
        return result

    async def _resolve_inner(self, home_team: str, away_team: str, start_time: str) -> str:
        bootstrap = await self._load_bootstrap()
        if not bootstrap:
            return ""

        date_from, date_to = ggbet_date_window(start_time, self.settings.tz)
        search_terms = unique_items([home_team, away_team, f"{home_team} {away_team}"])
        candidates: list[str] = []

        for search in search_terms:
            rows = await self._search(bootstrap, search, date_from, date_to)
            for slug in rows:
                if slug not in candidates:
                    candidates.append(slug)

        for slug in candidates:
            if not ggbet_slug_matches(slug, home_team, away_team):
                continue
            if await self._validate_slug(bootstrap, slug):
                return self._url_for_slug(slug)

        for slug in ggbet_slug_candidates(home_team, away_team, start_time, self.settings.tz):
            if await self._validate_slug(bootstrap, slug):
                return self._url_for_slug(slug)

        return ""

    async def _load_bootstrap(self) -> dict[str, str]:
        if self._bootstrap:
            return self._bootstrap

        locale = normalize_ggbet_locale(getattr(self.settings, "ggbet_locale", "en"))
        timeout = aiohttp.ClientTimeout(total=max(4, int(getattr(self.settings, "http_timeout_seconds", 12) or 12)))
        html = ""
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for template in self.bootstrap_paths:
                url = template.format(locale=locale)
                async with session.get(url, allow_redirects=True) as response:
                    if response.status < 400:
                        html = await response.text()
                        break
                    logger.warning("GGBET bootstrap HTTP %s url=%s", response.status, url)

        if not html:
            return {}

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if not match:
            logger.warning("GGBET bootstrap: __NEXT_DATA__ not found")
            return {}

        data = json.loads(unescape(match.group(1)))
        page_props = data.get("props", {}).get("pageProps", {}) or {}
        env = page_props.get("env", {}) or {}
        client = page_props.get("bettingClientOptions", {}) or {}

        endpoint = str(client.get("endpoint") or "").strip()
        token = str(client.get("token") or "").strip()
        app_id = str(env.get("BETTING_APP_ID_HEADER") or "").strip()
        access_token = str(env.get("BETTING_ACCESS_TOKEN") or "").strip()
        if not endpoint or not token or not app_id or not access_token:
            logger.warning("GGBET bootstrap incomplete: endpoint/token/app headers missing")
            return {}

        if endpoint.startswith("//"):
            endpoint = f"https:{endpoint}"

        self._bootstrap = {
            "endpoint": endpoint.rstrip("/") + "/graphql",
            "token": token,
            "app_id": app_id,
            "access_token": access_token,
            "locale": locale,
        }
        return self._bootstrap

    async def _graphql(self, bootstrap: dict[str, str], query: str, variables: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "content-type": "application/json",
            "X-Auth-Token": bootstrap["token"],
            "X-App-Id": bootstrap["app_id"],
            "X-App-Access-Token": bootstrap["access_token"],
        }
        timeout = aiohttp.ClientTimeout(total=max(4, int(getattr(self.settings, "http_timeout_seconds", 12) or 12)))
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(bootstrap["endpoint"], json={"query": query, "variables": variables}) as response:
                data = await response.json(content_type=None)
                if response.status >= 400 or data.get("errors"):
                    logger.info("GGBET GraphQL error status=%s errors=%s", response.status, data.get("errors"))
                    return {}
                return data

    async def _search(self, bootstrap: dict[str, str], search: str, date_from: str, date_to: str) -> list[str]:
        query = """
        query Search($offset:Int!,$limit:Int!,$search:String,$dateFrom:String,$dateTo:String) {
          matches: sportEventListByFilters(offset:$offset, limit:$limit, searchString:$search, dateFrom:$dateFrom, dateTo:$dateTo) {
            sportEvents { id slug __typename }
          }
        }
        """
        data = await self._graphql(
            bootstrap,
            query,
            {"offset": 0, "limit": 25, "search": search, "dateFrom": date_from, "dateTo": date_to},
        )
        rows = data.get("data", {}).get("matches", {}).get("sportEvents", []) if data else []
        return [str(row.get("slug") or "") for row in rows if row.get("slug")]

    async def _validate_slug(self, bootstrap: dict[str, str], slug: str) -> bool:
        query = """
        query GetMatchBySlug($slug:String!) {
          match: sportEventBySlug(slug:$slug) { id slug __typename }
        }
        """
        data = await self._graphql(bootstrap, query, {"slug": slug})
        match = data.get("data", {}).get("match") if data else None
        return bool(match and match.get("slug") == slug and match.get("id"))

    def _url_for_slug(self, slug: str) -> str:
        locale = normalize_ggbet_locale(getattr(self.settings, "ggbet_locale", "en"))
        return f"https://ggbet.ua/{locale}/sports/match/{slug}"

def extract_candidates(data: dict[str, Any]) -> list[dict[str, str]]:
    """Достать ссылки из organic_results и sitelinks SerpAPI."""
    result: list[dict[str, str]] = []

    for row in data.get("organic_results", []) or []:
        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or "")
        link = str(row.get("link") or "")
        if link:
            result.append({"link": link, "title": title, "snippet": snippet})

        sitelinks = row.get("sitelinks", {}) or {}
        for item in (sitelinks.get("inline", []) or []) + (sitelinks.get("expanded", []) or []):
            item_link = str(item.get("link") or "")
            if item_link:
                result.append(
                    {
                        "link": item_link,
                        "title": str(item.get("title") or title),
                        "snippet": snippet,
                    }
                )

    return result


def clean_url(value: str, allowed_domains: tuple[str, ...]) -> str:
    """Проверить домен и убрать query/fragment."""
    if not value:
        return ""

    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"}:
        return ""

    host = parsed.netloc.lower().replace("www.", "")
    if not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        return ""

    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_text(value: str) -> str:
    """Нормализовать текст команды/URL для сравнения."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\b(fc|cf|sc|afc|ac|club|football|soccer)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_ggbet_locale(value: str) -> str:
    raw = str(value or "en").strip().lower()
    return "uk-ua" if raw in {"uk", "ua", "uk-ua"} else "en"


def ggbet_date_window(start_time: str, tz_name: str) -> tuple[str, str]:
    start = parse_datetime_to_utc(start_time)
    try:
        target_tz = ZoneInfo(tz_name or "Europe/Kiev")
    except Exception:
        target_tz = ZoneInfo("Europe/Kiev")

    if start:
        local_day = start.astimezone(target_tz).date()
    else:
        local_day = datetime.now(target_tz).date()

    date_from = datetime(local_day.year, local_day.month, local_day.day, 0, 0, tzinfo=target_tz)
    date_to = date_from + timedelta(days=1)
    return date_from.isoformat(), date_to.isoformat()


def parse_datetime_to_utc(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def ggbet_slug_candidates(home_team: str, away_team: str, start_time: str, tz_name: str) -> list[str]:
    home_variants = ggbet_team_slug_variants(home_team)
    away_variants = ggbet_team_slug_variants(away_team)
    suffix = ""
    start = parse_datetime_to_utc(start_time)
    if start:
        try:
            local = start.astimezone(ZoneInfo(tz_name or "Europe/Kiev"))
        except Exception:
            local = start.astimezone(ZoneInfo("Europe/Kiev"))
        suffix = f"-{local.strftime('%d-%m')}"

    return unique_items([f"{home}-vs-{away}{suffix}" for home in home_variants for away in away_variants])


def ggbet_team_slug_variants(value: str) -> list[str]:
    base = slugify_ggbet_team(value)
    variants = [base]
    alias = GGBET_TEAM_SLUG_ALIASES.get(base)
    if alias:
        variants.insert(0, alias)
    if base and not base.startswith("vv-"):
        variants.append(f"vv-{base}")
    return unique_items([variant for variant in variants if variant])


def slugify_ggbet_team(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\b(club|football|soccer)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    slug = re.sub(r"-+", "-", text).strip("-")
    return slug or "team"


def ggbet_slug_matches(slug: str, home_team: str, away_team: str) -> bool:
    haystack = normalize_text(slug.replace("-", " "))
    return teams_match(haystack, normalize_text(home_team), normalize_text(away_team))


def unique_items(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


GGBET_TEAM_SLUG_ALIASES = {
    "ijsselmeervogels": "vv-ijsselmeervogels",
}


def teams_match(haystack: str, home_norm: str, away_norm: str) -> bool:
    """Проверить, что найденная страница относится именно к двум командам."""
    if not haystack or not home_norm or not away_norm:
        return False

    return token_match(haystack, home_norm) and token_match(haystack, away_norm)


def token_match(haystack: str, team_norm: str) -> bool:
    """Нестрогое совпадение команды по значимым токенам."""
    tokens = [t for t in team_norm.split() if len(t) >= 3]
    if not tokens:
        return False

    if team_norm in haystack:
        return True

    if len(tokens) == 1:
        return tokens[0] in haystack

    matched = sum(1 for token in tokens if token in haystack)
    return matched >= 2
