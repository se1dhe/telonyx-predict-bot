from __future__ import annotations
from app.config import get_settings
from app.services.api_football import ApiFootballClient
from app.services.free_data_provider import FreeDataProvider

def get_data_provider() -> ApiFootballClient | FreeDataProvider:
    """Вернуть источник данных по DATA_PROVIDER."""
    return ApiFootballClient() if get_settings().provider_normalized == "API_FOOTBALL" else FreeDataProvider()
