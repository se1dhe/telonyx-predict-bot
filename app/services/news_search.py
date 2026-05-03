from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from app.config import get_settings


logger = logging.getLogger(__name__)


class NewsSearchClient:
    """Поиск новостей через SerpAPI."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def search(self, home_team: str, away_team: str, league_name: str) -> list[dict[str, Any]]:
        """Найти новости по травмам, составам, дисквалификациям и изменениям.

        Важно: новости не должны блокировать весь pipeline.
        Если SerpAPI долго отвечает или падает — просто возвращаем пустой список.
        """
        if not self.settings.serpapi_key:
            return []

        try:
            return await asyncio.wait_for(
                self._search_inner(home_team, away_team, league_name),
                timeout=self.settings.news_timeout_seconds,
            )
        except Exception as exc:
            logger.warning("SerpAPI пропущен для %s — %s: %s", home_team, away_team, exc)
            return []

    async def _search_inner(self, home_team: str, away_team: str, league_name: str) -> list[dict[str, Any]]:
        query = f'"{home_team}" "{away_team}" {league_name} injuries lineup team news suspension football'
        params = {
            "engine": "google",
            "q": query,
            "api_key": self.settings.serpapi_key,
            "num": 5,
            "hl": "en",
        }

        timeout = aiohttp.ClientTimeout(total=self.settings.news_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://serpapi.com/search.json", params=params) as response:
                if response.status >= 400:
                    return []
                data = await response.json()

        result: list[dict[str, Any]] = []
        for row in data.get("organic_results", [])[:5]:
            result.append(
                {
                    "title": row.get("title"),
                    "link": row.get("link"),
                    "snippet": row.get("snippet"),
                    "source": row.get("source"),
                    "date": row.get("date"),
                }
            )
        return result
