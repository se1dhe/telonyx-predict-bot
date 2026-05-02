from __future__ import annotations
from typing import Any
import aiohttp
from app.config import get_settings

class NewsSearchClient:
    """Поиск новостей через SerpAPI."""
    def __init__(self) -> None:
        self.settings = get_settings()

    async def search(self, home_team: str, away_team: str, league_name: str) -> list[dict[str, Any]]:
        """Найти новости по травмам, составам и изменениям."""
        if not self.settings.serpapi_key:
            return []
        query = f'"{home_team}" "{away_team}" {league_name} injuries lineup team news suspension football'
        params = {"engine": "google", "q": query, "api_key": self.settings.serpapi_key, "num": 6, "hl": "en"}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://serpapi.com/search.json", params=params) as response:
                if response.status >= 400:
                    return []
                data = await response.json()
        return [{"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet"), "source": r.get("source"), "date": r.get("date")}
                for r in data.get("organic_results", [])[:6]]
