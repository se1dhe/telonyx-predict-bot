from __future__ import annotations
from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Настройки проекта."""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")

    # Legacy-переменные оставлены для совместимости.
    telegram_private_channel_id: str = Field("", alias="TELEGRAM_PRIVATE_CHANNEL_ID")
    telegram_public_channel: str = Field("@telonyx_predict", alias="TELEGRAM_PUBLIC_CHANNEL")

    # Языковые каналы. Если значение пустое — этот канал не обрабатывается.
    telegram_private_channel_uk: str = Field("", alias="TELEGRAM_PRIVATE_CHANNEL_UK")
    telegram_private_channel_en: str = Field("", alias="TELEGRAM_PRIVATE_CHANNEL_EN")
    telegram_private_channel_ru: str = Field("", alias="TELEGRAM_PRIVATE_CHANNEL_RU")
    telegram_public_channel_uk: str = Field("", alias="TELEGRAM_PUBLIC_CHANNEL_UK")
    telegram_public_channel_en: str = Field("", alias="TELEGRAM_PUBLIC_CHANNEL_EN")
    telegram_public_channel_ru: str = Field("", alias="TELEGRAM_PUBLIC_CHANNEL_RU")

    default_language: str = Field("uk", alias="DEFAULT_LANGUAGE")
    supported_languages_raw: str = Field("uk,en,ru", alias="SUPPORTED_LANGUAGES")

    telegram_bot_username: str = Field("telonyx_predict_bot", alias="TELEGRAM_BOT_USERNAME")
    public_channel_cta_enabled: bool = Field(True, alias="PUBLIC_CHANNEL_CTA_ENABLED")
    project_public_url: str = Field("https://predict.telonyx.app", alias="PROJECT_PUBLIC_URL")

    price_1_day_usdt: float = Field(2.99, alias="PRICE_1_DAY_USDT")
    price_3_days_usdt: float = Field(5.99, alias="PRICE_3_DAYS_USDT")
    price_30_days_usdt: float = Field(19.99, alias="PRICE_30_DAYS_USDT")
    stars_per_usdt: int = Field(50, alias="STARS_PER_USDT")

    stars_provider_token: str = Field("", alias="STARS_PROVIDER_TOKEN")

    paykassa_enabled: bool = Field(False, alias="PAYKASSA_ENABLED")
    paykassa_sci_id: str = Field("", alias="PAYKASSA_SCI_ID")
    paykassa_sci_key: str = Field("", alias="PAYKASSA_SCI_KEY")
    paykassa_system: str = Field("TRON_TRC20", alias="PAYKASSA_SYSTEM")
    paykassa_currency: str = Field("USDT", alias="PAYKASSA_CURRENCY")
    paykassa_test_mode: bool = Field(False, alias="PAYKASSA_TEST_MODE")
    paykassa_endpoint: str = Field("https://paykassa.pro/sci/0.3/index.php", alias="PAYKASSA_ENDPOINT")

    web_host: str = Field("0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(8080, alias="PORT")
    subscription_check_interval_minutes: int = Field(15, alias="SUBSCRIPTION_CHECK_INTERVAL_MINUTES")
    subscription_kick_enabled: bool = Field(True, alias="SUBSCRIPTION_KICK_ENABLED")
    subscription_notify_enabled: bool = Field(True, alias="SUBSCRIPTION_NOTIFY_ENABLED")
    styled_buttons_enabled: bool = Field(True, alias="STYLED_BUTTONS_ENABLED")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-5.5", alias="OPENAI_MODEL")
    ai_enabled: bool = Field(True, alias="AI_ENABLED")
    ai_fallback_on_error: bool = Field(True, alias="AI_FALLBACK_ON_ERROR")
    log_ai_reasoning: bool = Field(True, alias="LOG_AI_REASONING")
    show_tech_diagnostics: bool = Field(False, alias="SHOW_TECH_DIAGNOSTICS")
    show_detailed_picks: bool = Field(True, alias="SHOW_DETAILED_PICKS")
    bookmaker_link_enabled: bool = Field(True, alias="BOOKMAKER_LINK_ENABLED")
    bookmaker_name: str = Field("DraftKings", alias="BOOKMAKER_NAME")
    bookmaker_search_url_template: str = Field("", alias="BOOKMAKER_SEARCH_URL_TEMPLATE")
    bookmaker_backup_links_enabled: bool = Field(False, alias="BOOKMAKER_BACKUP_LINKS_ENABLED")
    bookmaker_backup_links: str = Field("", alias="BOOKMAKER_BACKUP_LINKS")
    bookmaker_market_hint: str = Field("Ищи рынок: Total Goals / Over-Under / Тотал голов", alias="BOOKMAKER_MARKET_HINT")
    draftkings_resolver_enabled: bool = Field(True, alias="DRAFTKINGS_RESOLVER_ENABLED")
    draftkings_resolver_max_results: int = Field(5, alias="DRAFTKINGS_RESOLVER_MAX_RESULTS")
    serpapi_key: str | None = Field(None, alias="SERPAPI_KEY")
    database_url: str = Field("sqlite+aiosqlite:///./data/bot.db", alias="DATABASE_URL")
    tz: str = Field("Europe/Kiev", alias="TZ")
    daily_run_hour: int = Field(5, alias="DAILY_RUN_HOUR")
    daily_stats_hour: int = Field(23, alias="DAILY_STATS_HOUR")
    daily_stats_minute: int = Field(55, alias="DAILY_STATS_MINUTE")
    stats_report_enabled: bool = Field(True, alias="STATS_REPORT_ENABLED")
    stats_after_each_finished_match_enabled: bool = Field(True, alias="STATS_AFTER_EACH_FINISHED_MATCH_ENABLED")
    matches_per_day: int = Field(5, alias="MATCHES_PER_DAY")
    min_ai_confidence: int = Field(45, alias="MIN_AI_CONFIDENCE")
    run_on_start: bool = Field(False, alias="RUN_ON_START")
    max_raw_events: int = Field(10, alias="MAX_RAW_EVENTS")
    max_candidates_for_ai: int = Field(5, alias="MAX_CANDIDATES_FOR_AI")
    allowed_countries_raw: str = Field("", alias="ALLOWED_COUNTRIES")
    preferred_league_ids_raw: str = Field("", alias="PREFERRED_LEAGUE_IDS")
    data_provider: str = Field("LOCAL", alias="DATA_PROVIDER")
    apifootball_key: str | None = Field(None, alias="APIFOOTBALL_KEY")
    apifootball_host: str = Field("v3.football.api-sports.io", alias="APIFOOTBALL_HOST")
    apifootball_free_plan: bool = Field(True, alias="APIFOOTBALL_FREE_PLAN")
    apifootball_season: int = Field(2025, alias="APIFOOTBALL_SEASON")
    local_league_codes_raw: str = Field("E0,E1,SP1,I1,D1,F1,N1,P1,SC0", alias="LOCAL_LEAGUE_CODES")
    local_lookahead_days: int = Field(1, alias="LOCAL_LOOKAHEAD_DAYS")
    local_min_form_matches: int = Field(4, alias="LOCAL_MIN_FORM_MATCHES")
    clubelo_enabled: bool = Field(True, alias="CLUBELO_ENABLED")
    pipeline_timeout_seconds: int = Field(180, alias="PIPELINE_TIMEOUT_SECONDS")
    http_timeout_seconds: int = Field(12, alias="HTTP_TIMEOUT_SECONDS")
    news_timeout_seconds: int = Field(8, alias="NEWS_TIMEOUT_SECONDS")

    thesportsdb_enabled: bool = Field(True, alias="THESPORTSDB_ENABLED")
    thesportsdb_api_key: str = Field("1", alias="THESPORTSDB_API_KEY")
    thesportsdb_league_ids_raw: str = Field("4328,4335,4332,4331,4334,4337", alias="THESPORTSDB_LEAGUE_IDS")
    espn_enabled: bool = Field(True, alias="ESPN_ENABLED")
    espn_leagues_raw: str = Field("eng.1,esp.1,ita.1,ger.1,fra.1,ned.1,por.1,sco.1", alias="ESPN_LEAGUES")

    @property
    def supported_languages(self) -> List[str]:
        """Список языков, которые может показывать бот."""
        allowed = {"uk", "en", "ru"}
        values = [x.strip().lower() for x in self.supported_languages_raw.split(",") if x.strip()]
        result = [x for x in values if x in allowed]
        return result or ["uk"]

    def normalize_language(self, lang: str | None) -> str:
        """Вернуть поддерживаемый язык или язык по умолчанию."""
        value = (lang or "").strip().lower()
        if value in self.supported_languages:
            return value
        default = self.default_language.strip().lower()
        return default if default in self.supported_languages else self.supported_languages[0]

    def public_channel_for(self, lang: str) -> str:
        """Публичный канал для языка. Пустая строка означает: не публиковать."""
        value = {
            "uk": self.telegram_public_channel_uk or self.telegram_public_channel,
            "en": self.telegram_public_channel_en,
            "ru": self.telegram_public_channel_ru,
        }.get(self.normalize_language(lang), "")
        return value.strip()

    def private_channel_for(self, lang: str) -> str:
        """Приватный канал для языка. Пустая строка означает: доступ недоступен."""
        value = {
            "uk": self.telegram_private_channel_uk or self.telegram_private_channel_id,
            "en": self.telegram_private_channel_en,
            "ru": self.telegram_private_channel_ru,
        }.get(self.normalize_language(lang), "")
        return value.strip()

    @property
    def active_public_languages(self) -> List[str]:
        """Языки, для которых заполнены публичные каналы."""
        return [lang for lang in self.supported_languages if self.public_channel_for(lang)]

    @property
    def active_private_languages(self) -> List[str]:
        """Языки, для которых заполнены приватные каналы."""
        return [lang for lang in self.supported_languages if self.private_channel_for(lang)]

    @property
    def render_languages(self) -> List[str]:
        """Языки, для которых нужно подготовить тексты прогнозов."""
        langs = []
        for lang in self.supported_languages:
            if self.public_channel_for(lang) or self.private_channel_for(lang):
                langs.append(lang)
        return langs or [self.normalize_language(self.default_language)]


    @property
    def allowed_countries(self) -> List[str]:
        if not self.allowed_countries_raw.strip():
            return []
        return [x.strip().lower() for x in self.allowed_countries_raw.split(",") if x.strip()]

    @property
    def preferred_league_ids(self) -> List[int]:
        if not self.preferred_league_ids_raw.strip():
            return []
        return [int(x.strip()) for x in self.preferred_league_ids_raw.split(",") if x.strip().isdigit()]

    @property
    def local_league_codes(self) -> List[str]:
        if not self.local_league_codes_raw.strip():
            return []
        return [x.strip().upper() for x in self.local_league_codes_raw.split(",") if x.strip()]


    @property
    def thesportsdb_league_ids(self) -> List[str]:
        """ID лиг TheSportsDB для fallback-расписания."""
        if not self.thesportsdb_league_ids_raw.strip():
            return []
        return [x.strip() for x in self.thesportsdb_league_ids_raw.split(",") if x.strip()]


    @property
    def espn_leagues(self) -> List[str]:
        """Коды футбольных лиг ESPN для fallback-расписания."""
        if not self.espn_leagues_raw.strip():
            return []
        return [x.strip() for x in self.espn_leagues_raw.split(",") if x.strip()]

    @property
    def provider_normalized(self) -> str:
        value = self.data_provider.strip().upper()
        return value if value in {"LOCAL", "API_FOOTBALL"} else "LOCAL"

@lru_cache
def get_settings() -> Settings:
    return Settings()
