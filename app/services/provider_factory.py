from __future__ import annotations
from app.config import get_settings
from app.services.api_football import ApiFootballClient
from app.services.free_data_provider import FreeDataProvider

def get_data_provider() -> ApiFootballClient | FreeDataProvider:
    """Вернуть источник данных по DATA_PROVIDER."""
    settings = get_settings()
    if settings.odds_first_enabled:
        return ApiFootballClient()
    return ApiFootballClient() if settings.provider_normalized == "API_FOOTBALL" else FreeDataProvider()
