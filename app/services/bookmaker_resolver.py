from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

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

    async def resolve(self, home_team: str, away_team: str) -> tuple[str, str]:
        """Вернуть tuple(url, provider_name) или ("", "")."""
        if not self.settings.bookmaker_link_enabled:
            return "", ""

        if not self.settings.serpapi_key:
            logger.warning("Bookmaker resolver skipped: SERPAPI_KEY is empty")
            return "", ""

        home_team = str(home_team or "").strip()
        away_team = str(away_team or "").strip()
        if not home_team or not away_team:
            return "", ""

        cache_key = f"{normalize_text(home_team)}::{normalize_text(away_team)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        provider_keys = self._provider_order()
        try:
            result = await asyncio.wait_for(
                self._resolve_inner(home_team, away_team, provider_keys),
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

    async def _resolve_inner(self, home_team: str, away_team: str, provider_keys: list[str]) -> tuple[str, str]:
        for provider_key in provider_keys:
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
