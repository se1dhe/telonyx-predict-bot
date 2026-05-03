from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.config import get_settings


logger = logging.getLogger(__name__)

DRAFTKINGS_EVENT_RE = re.compile(
    r"^https?://sportsbook\.draftkings\.com/event/[^?#]+/\d+(?:[/?#].*)?$",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    """Нормализовать название команды для сравнения."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def draftkings_slug(home: str, away: str) -> str:
    """Собрать DraftKings slug.

    Пример:
    Auxerre + Angers -> auxerre-vs-angers
    """
    raw = f"{home} vs {away}"
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


class DraftKingsResolver:
    """Поиск точной DraftKings ссылки через SerpAPI.

    Почему так:
    DraftKings event URL имеет вид:
    https://sportsbook.draftkings.com/event/auxerre-vs-angers/34020984

    Последняя часть — внутренний event_id DraftKings.
    Его нельзя получить из названия матча простым slug-ом.
    Поэтому используем SerpAPI/Google и ищем уже проиндексированную event URL.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._cache: dict[str, str] = {}

    async def resolve(self, home_team: str, away_team: str) -> str:
        """Вернуть точный DraftKings event URL или пустую строку."""
        if not self.settings.draftkings_resolver_enabled:
            return ""

        if not self.settings.serpapi_key:
            logger.warning("DraftKings resolver skipped: SERPAPI_KEY is empty")
            return ""

        key = f"{normalize_text(home_team)}::{normalize_text(away_team)}"
        if key in self._cache:
            return self._cache[key]

        try:
            url = await asyncio.wait_for(
                self._resolve_inner(home_team, away_team),
                timeout=self.settings.news_timeout_seconds,
            )
        except Exception as exc:
            logger.warning("DraftKings resolver failed for %s — %s: %s", home_team, away_team, exc)
            url = ""

        self._cache[key] = url
        return url

    async def _resolve_inner(self, home_team: str, away_team: str) -> str:
        slug = draftkings_slug(home_team, away_team)

        queries = [
            f'site:sportsbook.draftkings.com/event/{slug} DraftKings',
            f'site:sportsbook.draftkings.com/event "{home_team}" "{away_team}" DraftKings',
            f'site:sportsbook.draftkings.com/event "{home_team} vs {away_team}"',
        ]

        for query in queries:
            url = await self._search_one(query, home_team, away_team)
            if url:
                logger.info("DraftKings event URL resolved: %s — %s -> %s", home_team, away_team, url)
                return url

        logger.info("DraftKings event URL not found for %s — %s", home_team, away_team)
        return ""

    async def _search_one(self, query: str, home_team: str, away_team: str) -> str:
        params = {
            "engine": "google",
            "q": query,
            "api_key": self.settings.serpapi_key,
            "num": self.settings.draftkings_resolver_max_results,
            "hl": "en",
        }

        timeout = aiohttp.ClientTimeout(total=self.settings.news_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://serpapi.com/search.json", params=params) as response:
                if response.status >= 400:
                    logger.warning("SerpAPI DraftKings search HTTP %s for query=%s", response.status, query)
                    return ""

                data = await response.json()

        candidates: list[str] = []

        for row in data.get("organic_results", []):
            for field in ("link", "displayed_link"):
                link = row.get(field)
                if link:
                    candidates.append(str(link))

            # Иногда Google кладёт ссылки в sitelinks.
            sitelinks = row.get("sitelinks", {})
            for item in sitelinks.get("inline", []) + sitelinks.get("expanded", []):
                link = item.get("link")
                if link:
                    candidates.append(str(link))

        home_norm = normalize_text(home_team)
        away_norm = normalize_text(away_team)

        for link in candidates:
            clean_link = clean_draftkings_url(link)
            if not clean_link:
                continue

            path_norm = normalize_text(urlparse(clean_link).path)

            if home_norm in path_norm and away_norm in path_norm:
                return clean_link

        # Если точное совпадение не найдено, но есть валидная DK event URL — возвращаем её только
        # когда slug максимально похож, чтобы не подставить чужой матч.
        expected_slug = draftkings_slug(home_team, away_team)
        for link in candidates:
            clean_link = clean_draftkings_url(link)
            if clean_link and expected_slug in clean_link.lower():
                return clean_link

        return ""


def clean_draftkings_url(value: str) -> str:
    """Оставить только валидную DraftKings event URL с event_id."""
    if not value:
        return ""

    value = value.split("&")[0].strip()

    if not DRAFTKINGS_EVENT_RE.match(value):
        return ""

    # Убираем query/fragment.
    parsed = urlparse(value)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
