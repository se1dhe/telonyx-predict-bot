from __future__ import annotations
import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from html import escape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import aiohttp
from sqlalchemy import select
from app.config import get_settings
from app.db import SessionLocal
from app.models import DailyRun, Prediction
from app.schemas import AiPick, CandidateContext
from app.services.ai_selector import AiSelector
from app.services.news_search import NewsSearchClient
from app.services.provider_factory import get_data_provider
from app.services.rule_based_selector import RuleBasedSelector
from app.services.render import render_daily_summary, render_pick_detail
from app.services.bookmaker_resolver import BookmakerResolver, clean_url, normalize_text, teams_match
from app.services.learning_calibrator import LearningCalibrator

logger = logging.getLogger(__name__)


@dataclass
class FixtureFilterStats:
    """Диагностика первичного фильтра матчей."""

    total: int = 0
    accepted: int = 0
    preferred: int = 0
    fallback: int = 0
    skipped_status: int = 0
    skipped_time: int = 0
    skipped_no_time: int = 0
    skipped_wrong_local_date: int = 0
    skipped_country: int = 0
    skipped_noise: int = 0


class DailyPipeline:
    """Джерело даних → фільтри → новини → AI → safe-mode/learning observer → база."""
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = get_data_provider()
        self.news = NewsSearchClient()
        self.ai = AiSelector()
        self.rule_based = RuleBasedSelector()
        self.bookmaker = BookmakerResolver()
        self.learning = LearningCalibrator()

    async def run_for_today(self, force: bool = False) -> tuple[dict[str, str], dict[str, list[str]]]:
        today = datetime.now(ZoneInfo(self.settings.tz)).date()
        return await self.run_for_date(today, force=force)

    async def run_for_date(self, target_date: date, force: bool = False) -> tuple[dict[str, str], dict[str, list[str]]]:
        date_key = target_date.isoformat()
        provider_name = "API_FOOTBALL" if (self.settings.odds_first_enabled or self.settings.ggbet_odds_first_enabled) else self.settings.provider_normalized
        ai_provider_name = (self.settings.ai_provider or "ai").strip().lower()
        if not force:
            cached = await self._load_existing_texts(date_key, provider_name)
            if cached:
                return cached
        debug = [f"📅 Дата: {date_key}", f"🧩 Джерело: {provider_name}"]
        try:
            logger.info("Pipeline: получаю матчи от источника %s на дату %s", provider_name, date_key)
            raw_fixtures = await self.provider.fixtures_by_date(target_date)
            logger.info("Pipeline: источник вернул матчей: %s", len(raw_fixtures))
        except Exception as exc:
            summary = f"⚠️ Не вдалося отримати матчі з джерела {provider_name}.\n\nПомилка: <code>{escape(str(exc)[:1000])}</code>"
            await self._save_daily_run(date_key, summary, 0)
            return self._single_language_result(summary, [])
        debug.append(f"📦 Матчів від джерела: {len(raw_fixtures)}")
        raw_fixtures, filter_stats = self._filter_raw_fixtures(raw_fixtures, target_date)
        debug.append(f"🔎 Після первинного фільтра: {len(raw_fixtures)}")
        debug.append(
            "🧮 Фільтр: "
            f"preferred={filter_stats.preferred}, fallback={filter_stats.fallback}, "
            f"status={filter_stats.skipped_status}, time={filter_stats.skipped_time}, "
            f"no_time={filter_stats.skipped_no_time}, local_date={filter_stats.skipped_wrong_local_date}, "
            f"country={filter_stats.skipped_country}, noise={filter_stats.skipped_noise}"
        )
        contexts: list[CandidateContext] = []
        errors, rejected = [], []

        for fixture in raw_fixtures[:self.settings.max_raw_events]:
            try:
                ctx = await asyncio.wait_for(
                    self.provider.build_context(fixture),
                    timeout=max(3, self.settings.context_timeout_seconds),
                )

                if ctx.pre_ai_score < self.settings.min_context_pre_ai_score or ctx.data_quality_score < self.settings.min_context_data_quality:
                    rejected.append(f"{ctx.home_team} — {ctx.away_team}: data={ctx.data_quality_score}, pre_ai={ctx.pre_ai_score}, league={ctx.league_name}")
                    continue

                contexts.append(ctx)
            except asyncio.TimeoutError:
                errors.append(f"{safe_fixture_title(fixture)}: context timeout after {self.settings.context_timeout_seconds}s")
                logger.warning("Контекст матчу пропущен по timeout: %s", safe_fixture_title(fixture))
            except Exception as exc:
                errors.append(f"{safe_fixture_title(fixture)}: {str(exc)[:250]}")
                logger.exception("Помилка під час збору контексту матчу")

        contexts.sort(key=lambda c: c.pre_ai_score, reverse=True)
        contexts = contexts[:self.settings.max_candidates_for_ai]

        if self.settings.news_enabled and self.settings.serpapi_key and contexts:
            news_limit = max(0, min(self.settings.news_for_top_candidates, len(contexts)))
            for ctx in contexts[:news_limit]:
                try:
                    ctx.news = await asyncio.wait_for(
                        self.news.search(ctx.home_team, ctx.away_team, ctx.league_name),
                        timeout=max(2, self.settings.news_timeout_seconds),
                    )
                except asyncio.TimeoutError:
                    logger.warning("SerpAPI news timeout для %s — %s", ctx.home_team, ctx.away_team)
                    ctx.news = []
                except Exception as exc:
                    logger.warning("SerpAPI news error для %s — %s: %s", ctx.home_team, ctx.away_team, exc)
                    ctx.news = []
        debug += [f"🧠 Контекстів зібрано: {len(contexts)}", f"🗑 Відсіяно за score: {len(rejected)}", f"❌ Помилок під час збору: {len(errors)}"]
        if errors:
            debug.append("\n<b>Перші помилки:</b>")
            debug += [f"• {escape(x)}" for x in errors[:5]]
        if rejected:
            debug.append("\n<b>Перші відсіяні:</b>")
            debug += [f"• {escape(x)}" for x in rejected[:5]]
        if not contexts:
            summary = "⚠️ На сьогодні не знайдено достатньо якісних матчів.\n\n<b>Діагностика:</b>\n" + "\n".join(debug)
            await self._save_daily_run(date_key, summary, 0)
            return self._no_quality_matches_result()
        if self.settings.ai_enabled:
            try:
                logger.info("Pipeline: отправляю %s кандидатов в AI provider=%s", len(contexts), ai_provider_name)
                ai_response = await self.ai.select_gold_matches(contexts)
                logger.info("Pipeline: AI provider=%s вернул выбранных матчей: %s", ai_provider_name, len(ai_response.selected))
            except Exception as exc:
                safe_error = escape(str(exc)[:1000])

                if self.settings.ai_fallback_on_error:
                    logger.exception("AI provider=%s не смог выбрать матчи, включаю rule-based fallback", ai_provider_name)
                    debug.append(f"🧠 AI provider={escape(ai_provider_name)}: ошибка, используется локальный fallback")
                    debug.append(f"⚠️ AI error: <code>{safe_error}</code>")
                    ai_response = await self.rule_based.select_gold_matches(contexts)
                else:
                    summary = (
                        "⚠️ AI не зміг вибрати матчі.\n\n"
                        "<b>Діагностика:</b>\n"
                        + "\n".join(debug)
                        + f"\n\n<b>Помилка AI:</b>\n<code>{safe_error}</code>"
                    )
                    await self._save_daily_run(date_key, summary, 0)
                    return self._single_language_result(summary, [])
        else:
            logger.info("Pipeline: AI отключён, использую локальный rule-based selector")
            debug.append("🧠 AI: отключён, используется локальный алгоритм")
            ai_response = await self.rule_based.select_gold_matches(contexts)

        picks = self._enrich_picks_with_context(ai_response.selected, contexts)

        try:
            picks, learning_rejected = await self.learning.apply(picks, contexts)
            if learning_rejected:
                ai_response.rejected_summary.extend([f"Learning: {item}" for item in learning_rejected])
                debug.append(f"🧬 Safe-mode відсіяв/змінив: {len(learning_rejected)}")
        except Exception as exc:
            logger.exception("Learning calibrator failed, continue without calibration")
            debug.append(f"⚠️ Learning calibrator error: <code>{escape(str(exc)[:500])}</code>")

        picks, odds_rejected = self._filter_picks_by_min_odds(picks, contexts)
        if odds_rejected:
            ai_response.rejected_summary.extend([f"Odds: {item}" for item in odds_rejected])
            debug.append(f"💸 Odds-фільтр відсіяв: {len(odds_rejected)}")

        ctx_by_id_for_sort = {c.fixture_id: c for c in contexts}
        picks.sort(key=lambda p: pick_start_sort_timestamp(p, ctx_by_id_for_sort))

        await self._resolve_bookmaker_links(picks, ctx_by_id_for_sort)
        self._log_selected_picks(picks)
        debug.append(f"✅ Вибрано матчів: {len(picks)}")
        if not picks:
            summary = "⚠️ AI/learning не вибрав жодного достатньо сильного матчу.\n\n<b>Діагностика:</b>\n" + "\n".join(debug)
            if ai_response.rejected_summary:
                summary += "\n\n<b>Rejected:</b>\n" + "\n".join(f"• {escape(x)}" for x in ai_response.rejected_summary[:8])
            await self._save_daily_run(date_key, summary, 0)
            return self._no_quality_matches_result()
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}

        summaries: dict[str, str] = {}
        details_by_lang: dict[str, list[str]] = {}

        for lang in self.settings.render_languages:
            summary = render_daily_summary(
                picks,
                ai_response.rejected_summary,
                provider=provider_name,
                contexts_by_id=ctx_by_id,
                lang=lang,
            )

            if self.settings.show_tech_diagnostics:
                summary += "\n\n<b>Technical diagnostics:</b>\n" + "\n".join(debug)

            summaries[lang] = summary
            details_by_lang[lang] = [render_pick_detail(p, ctx_by_id.get(p.fixture_id), lang=lang) for p in picks]

        logger.info("Техническая диагностика daily run:\n%s", "\n".join(debug))

        uk_summary = summaries.get("uk") or next(iter(summaries.values()))
        uk_details = details_by_lang.get("uk") or next(iter(details_by_lang.values()))
        await self._save_predictions(date_key, picks, uk_details, contexts, provider_name)
        await self._save_daily_run(date_key, uk_summary, len(picks))
        return summaries, details_by_lang

    def _single_language_result(self, summary: str, details: list[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
        return ({lang: summary for lang in self.settings.render_languages}, {lang: details for lang in self.settings.render_languages})

    def _no_quality_matches_result(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        summaries = {lang: render_daily_summary([], [], provider=self.settings.provider_normalized, contexts_by_id={}, lang=lang) for lang in self.settings.render_languages}
        return summaries, {lang: [] for lang in self.settings.render_languages}

    def _split_pick_teams(self, pick: AiPick) -> tuple[str, str]:
        if "—" in pick.match_title:
            home, away = pick.match_title.split("—", 1)
            return home.strip(), away.strip()
        if " vs " in pick.match_title.lower():
            parts = re.split(r"\s+vs\s+", pick.match_title, flags=re.IGNORECASE)
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()
        return pick.match_title.strip(), ""

    async def _resolve_tracking_links(self, picks: list[AiPick], ctx_by_id: dict[str, CandidateContext]) -> None:
        """Найти точную SofaScore-страницу матча, если SerpAPI доступен."""
        if not self.settings.serpapi_key:
            return

        for pick in picks:
            home, away = self._split_pick_teams(pick)
            if not home or not away:
                continue

            url = await self._resolve_sofascore_url(home, away)
            if url:
                pick.tracking_url = url
                logger.info("SofaScore exact URL set for %s: %s", pick.match_title, url)

    async def _resolve_sofascore_url(self, home_team: str, away_team: str) -> str:
        query = f'site:sofascore.com/football/match "{home_team}" "{away_team}"'
        params = {
            "engine": "google",
            "q": query,
            "api_key": self.settings.serpapi_key,
            "num": 5,
            "hl": "en",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=max(3, self.settings.news_timeout_seconds))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://serpapi.com/search.json", params=params) as response:
                    if response.status >= 400:
                        logger.warning("SofaScore resolver HTTP %s for %s — %s", response.status, home_team, away_team)
                        return ""
                    data = await response.json(content_type=None)
        except Exception as exc:
            logger.warning("SofaScore resolver failed for %s — %s: %s", home_team, away_team, exc)
            return ""

        home_norm = normalize_text(home_team)
        away_norm = normalize_text(away_team)
        for candidate in extract_search_candidates(data):
            link = clean_url(candidate.get("link", ""), ("sofascore.com",))
            if not link:
                continue

            parsed = urlparse(link)
            if "/football/match/" not in parsed.path:
                continue

            haystack = normalize_text(
                " ".join([candidate.get("title", ""), candidate.get("snippet", ""), parsed.path])
            )
            if teams_match(haystack, home_norm, away_norm):
                return link

        return ""

    async def _resolve_bookmaker_links(self, picks: list[AiPick], ctx_by_id: dict[str, CandidateContext]) -> None:
        """Поставить точную/предсказуемую ссылку на линию букмекера."""
        if not self.settings.bookmaker_link_enabled:
            return

        resolver_provider = (self.settings.bookmaker_resolver_provider or "").strip().lower()
        if getattr(self.settings, "bookmaker_late_refresh_enabled", True) and resolver_provider != "ggbet":
            logger.info("Bookmaker resolving deferred until late refresh window")
            return

        for pick in picks:
            home, away = self._split_pick_teams(pick)
            if not home or not away:
                continue

            ctx = ctx_by_id.get(pick.fixture_id)
            url = ""
            provider_name = ""

            if ctx and "ggbet.ua/" in str(ctx.match_url or ""):
                url, provider_name = ctx.match_url, "GGBET"
            else:
                url, provider_name = await self.bookmaker.resolve(home, away, ctx.start_time if ctx else "")

            pick.bookmaker_url = url

            if provider_name:
                setattr(pick, "bookmaker_name", provider_name)

            if provider_name.lower() == "ggbet" and url and pick.bookmaker_odds is None:
                ggbet_odds = await self.bookmaker.ggbet_market_odds(url, pick.main_bet_code)
                pick.bookmaker_odds = ggbet_odds
                if ggbet_odds:
                    logger.info("GGBET odds set for %s %s: %.2f", pick.match_title, pick.main_bet_code, ggbet_odds)
                else:
                    logger.info("GGBET odds not found for %s %s; API-Football odds hidden", pick.match_title, pick.main_bet_code)

            if url:
                logger.info("Bookmaker exact URL set for %s: %s", pick.match_title, url)
            else:
                logger.info("Bookmaker exact URL not found for %s; bookmaker button hidden", pick.match_title)

    def _log_selected_picks(self, picks: list[AiPick]) -> None:
        if not self.settings.log_ai_reasoning:
            return

        for index, pick in enumerate(picks, start=1):
            logger.info(
                "SELECTED PICK #%s | match=%s | bet=%s | confidence=%s/100 | risk=%s | score=%s | source=%s",
                index, pick.match_title, pick.main_bet_label, pick.confidence, pick.risk_level, pick.expected_score, getattr(pick, "pick_source", ""),
            )
            logger.info("SELECTED PICK #%s | why=%s", index, pick.why_this_match_is_gold)
            logger.info("SELECTED PICK #%s | reasoning=%s", index, pick.reasoning)

    def _enrich_picks_with_context(self, picks: list[AiPick], contexts: list[CandidateContext]) -> list[AiPick]:
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}
        for pick in picks:
            ctx = ctx_by_id.get(pick.fixture_id)
            if not ctx:
                continue
            pick.tracking_url = ctx.tracking_url
        return picks

    def _filter_picks_by_min_odds(self, picks: list[AiPick], contexts: list[CandidateContext]) -> tuple[list[AiPick], list[str]]:
        """Отсеять прогнозы, если известный коэффициент выбранного рынка ниже MIN_PICK_ODDS."""
        min_odds = float(getattr(self.settings, "min_pick_odds", 0) or 0)
        if min_odds <= 1:
            return picks, []

        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}
        require_available = bool(getattr(self.settings, "min_pick_odds_require_available", False))
        result: list[AiPick] = []
        rejected: list[str] = []

        for pick in picks:
            ctx = ctx_by_id.get(pick.fixture_id)
            offer = find_pick_market_offer(pick, ctx)
            odds = offer["odd"] if offer else None

            if odds is None:
                if require_available:
                    rejected.append(f"{pick.match_title}: немає коефіцієнта для {pick.main_bet_code}")
                    continue
                result.append(pick)
                continue

            if odds < min_odds:
                rejected.append(f"{pick.match_title}: {pick.main_bet_code} коеф. {odds:.2f} нижче {min_odds:.2f}")
                continue

            warnings = list(pick.data_warnings or [])
            warnings.append(f"Коефіцієнт ринку: {odds:.2f}")
            result.append(
                pick.model_copy(
                    update={
                        "bookmaker_name": str(offer.get("bookmaker") or self.settings.bookmaker_name) if offer else self.settings.bookmaker_name,
                        "bookmaker_odds": odds,
                        "data_warnings": warnings[:6],
                    }
                )
            )

        logger.info(
            "Odds filter: input=%s output=%s rejected=%s min_odds=%.2f require_available=%s",
            len(picks),
            len(result),
            len(rejected),
            min_odds,
            require_available,
        )
        return result, rejected[:10]

    def _filter_raw_fixtures(self, fixtures: list, target_date: date) -> tuple[list, FixtureFilterStats]:
        allowed = self.settings.allowed_countries
        preferred = set(str(x) for x in self.settings.preferred_league_ids)
        preferred_items = []
        fallback_items = []
        stats = FixtureFilterStats(total=len(fixtures))

        now_utc = datetime.now(timezone.utc)
        min_start_utc = now_utc + timedelta(minutes=max(0, self.settings.min_match_start_lead_minutes))

        for fixture in fixtures:
            if isinstance(fixture, dict):
                status_short = fixture.get("fixture", {}).get("status", {}).get("short") or ""
                league = fixture.get("league", {}) or {}
                country = str(league.get("country", ""))
                league_id = str(league.get("id", ""))
                league_name = str(league.get("name", ""))
                start_utc = fixture_start_utc(fixture)
            else:
                status_value = getattr(fixture, "status", "")
                status_short = status_value.get("short") if isinstance(status_value, dict) else (status_value or "NS")
                country = str(getattr(fixture, "country", ""))
                league_id = str(getattr(fixture, "league_id", ""))
                league_name = str(getattr(fixture, "league_name", ""))
                start_utc = fixture_start_utc(fixture)

            if status_short and status_short not in {"NS"}:
                stats.skipped_status += 1
                continue
            if start_utc is None:
                stats.skipped_no_time += 1
                continue
            if start_utc < min_start_utc:
                stats.skipped_time += 1
                continue
            if fixture_local_date(start_utc, self.settings.tz) != target_date:
                stats.skipped_wrong_local_date += 1
                continue
            if allowed and country and country.lower() not in allowed:
                stats.skipped_country += 1
                continue

            text = f"{league_name} {country}".lower()
            if any(w in text for w in ["u17", "u19", "u20", "u21", "women", " w league", "feminino", "femenina", "ladies", "amateur", "esoccer", "virtual", "friendly"]):
                stats.skipped_noise += 1
                continue

            if preferred and league_id in preferred:
                preferred_items.append(fixture)
            else:
                fallback_items.append(fixture)

        preferred_items.sort(key=lambda f: fixture_sort_timestamp(f))
        fallback_items.sort(key=lambda f: fixture_sort_timestamp(f))

        limit = max(1, self.settings.max_raw_events)
        result = (preferred_items + fallback_items)[:limit]
        stats.preferred = min(len(preferred_items), limit)
        stats.fallback = max(0, len(result) - stats.preferred)
        stats.accepted = len(result)
        logger.info(
            "Fixture filter: total=%s accepted=%s preferred=%s fallback=%s skipped_status=%s skipped_time=%s skipped_no_time=%s skipped_wrong_local_date=%s skipped_country=%s skipped_noise=%s",
            stats.total, stats.accepted, stats.preferred, stats.fallback, stats.skipped_status, stats.skipped_time, stats.skipped_no_time, stats.skipped_wrong_local_date, stats.skipped_country, stats.skipped_noise,
        )
        return result, stats

    async def _load_existing_texts(self, date_key: str, provider_name: str) -> tuple[dict[str, str], dict[str, list[str]]] | None:
        async with SessionLocal() as session:
            run = (await session.execute(select(DailyRun).where(DailyRun.date_key == date_key))).scalar_one_or_none()
            preds = (await session.execute(select(Prediction).where(Prediction.date_key == date_key).where(Prediction.provider == provider_name).order_by(Prediction.start_time.asc(), Prediction.ai_rank_score.desc()))).scalars().all()
        if run and preds:
            return self._single_language_result(run.summary_text, [p.rendered_text for p in preds])
        return None

    async def _save_daily_run(self, date_key: str, summary: str, count: int) -> None:
        async with SessionLocal() as session:
            run = (await session.execute(select(DailyRun).where(DailyRun.date_key == date_key))).scalar_one_or_none()
            if run:
                run.summary_text = summary; run.selected_count = count; run.status = "done"
            else:
                session.add(DailyRun(date_key=date_key, status="done", selected_count=count, summary_text=summary))
            await session.commit()

    async def _save_predictions(self, date_key: str, picks: list[AiPick], details: list[str], contexts: list[CandidateContext], provider_name: str) -> None:
        ctx_by_id = {c.fixture_id: c for c in contexts}
        async with SessionLocal() as session:
            stale_rows = (await session.execute(
                select(Prediction)
                .where(Prediction.date_key == date_key)
                .where(Prediction.provider == provider_name)
            )).scalars().all()
            current_fixture_ids = {pick.fixture_id for pick in picks}
            for stale in stale_rows:
                if stale.fixture_id not in current_fixture_ids and not stale.is_finished:
                    stale.rendered_text = ""
                    stale.private_message_refs = ""
                    stale.public_message_refs = ""

            for index, pick in enumerate(picks):
                rendered = details[index] if index < len(details) else ""
                existing = (await session.execute(select(Prediction).where(Prediction.date_key == date_key).where(Prediction.provider == provider_name).where(Prediction.fixture_id == pick.fixture_id))).scalar_one_or_none()
                home, away = split_match_title(pick.match_title)
                ctx = ctx_by_id.get(pick.fixture_id)
                if existing:
                    existing.prediction_json = pick.model_dump_json(); existing.rendered_text = rendered
                    existing.main_bet_code = pick.main_bet_code; existing.main_bet_label = pick.main_bet_label
                    existing.confidence = pick.confidence; existing.ai_rank_score = pick.ai_rank_score
                    existing.bookmaker_url = pick.bookmaker_url
                    existing.bookmaker_name = pick.bookmaker_name or existing.bookmaker_name
                    existing.bookmaker_odds = format_bookmaker_odds(pick.bookmaker_odds)
                    existing.start_time = ctx.start_time if ctx else existing.start_time
                else:
                    session.add(Prediction(date_key=date_key, provider=provider_name, fixture_id=pick.fixture_id,
                        home_team=home, away_team=away, league_name=ctx.league_name if ctx else "",
                        country=ctx.country if ctx else "", start_time=ctx.start_time if ctx else "",
                        source_league_code=ctx.source_league_code if ctx else "", prediction_json=pick.model_dump_json(),
                        rendered_text=rendered, main_bet_code=pick.main_bet_code, main_bet_label=pick.main_bet_label,
                        confidence=pick.confidence, ai_rank_score=pick.ai_rank_score, bookmaker_url=pick.bookmaker_url,
                        bookmaker_name=pick.bookmaker_name, bookmaker_odds=format_bookmaker_odds(pick.bookmaker_odds)))
            await session.commit()


def pick_start_sort_timestamp(pick: AiPick, ctx_by_id: dict[str, CandidateContext]) -> tuple[int, int]:
    ctx = ctx_by_id.get(pick.fixture_id)
    ts = context_start_sort_timestamp(ctx)
    return ts, -int(getattr(pick, "ai_rank_score", 0) or 0)


def context_start_sort_timestamp(ctx: CandidateContext | None) -> int:
    if not ctx:
        return 9999999999
    parsed = parse_datetime_to_utc(str(getattr(ctx, "start_time", "") or ""))
    if parsed:
        return int(parsed.timestamp())
    return 9999999999


def fixture_start_utc(fixture: object) -> datetime | None:
    """Определить старт матча в UTC для dict и RawFixture.

    API-FOOTBALL в нашем RawFixture хранит date как ISO-строку и timestamp
    как unix timestamp. Старый код ошибочно ожидал date.year/date.month как у
    date-объекта, из-за чего время не парсилось и прошедшие матчи могли пройти.
    """
    try:
        if isinstance(fixture, dict):
            timestamp = fixture.get("fixture", {}).get("timestamp")
            if timestamp:
                return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            return parse_datetime_to_utc(str(fixture.get("fixture", {}).get("date") or ""))

        timestamp = getattr(fixture, "timestamp", None)
        if timestamp:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)

        raw_date = getattr(fixture, "date", None)
        if isinstance(raw_date, str):
            parsed = parse_datetime_to_utc(raw_date)
            if parsed:
                return parsed

        fixture_date = raw_date
        fixture_time = str(getattr(fixture, "time", "") or "").strip()
        if fixture_date and hasattr(fixture_date, "year"):
            if fixture_time and len(fixture_time) >= 5:
                hh, mm = fixture_time[:5].split(":", 1)
                return datetime(fixture_date.year, fixture_date.month, fixture_date.day, int(hh), int(mm), tzinfo=timezone.utc)
            return datetime(fixture_date.year, fixture_date.month, fixture_date.day, 23, 59, tzinfo=timezone.utc)
    except Exception as exc:
        logger.warning("Failed to parse fixture start time for %s: %s", safe_fixture_title(fixture), exc)
        return None
    return None


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


def fixture_local_date(start_utc: datetime, tz_name: str) -> date:
    """Дата матча в пользовательской таймзоне, а не дата API/UTC."""
    try:
        target_tz = ZoneInfo(tz_name or "Europe/Kiev")
    except Exception:
        target_tz = ZoneInfo("Europe/Kiev")
    return start_utc.astimezone(target_tz).date()


def fixture_sort_key(fixture: object) -> int:
    return fixture_sort_timestamp(fixture)


def fixture_sort_timestamp(fixture: object) -> int:
    start = fixture_start_utc(fixture)
    if start is not None:
        return int(start.timestamp())
    if isinstance(fixture, dict):
        return int(fixture.get("fixture", {}).get("timestamp") or 9999999999)
    return int(getattr(fixture, "timestamp", None) or 9999999999)


def find_pick_market_odds(pick: AiPick, ctx: CandidateContext | None) -> float | None:
    """Найти коэффициент именно для выбранного рынка, если источник его дал."""
    offer = find_pick_market_offer(pick, ctx)
    return float(offer["odd"]) if offer else None


def find_pick_market_offer(pick: AiPick, ctx: CandidateContext | None) -> dict[str, object] | None:
    """Найти лучшую букмекерскую линию именно для выбранного рынка."""
    if not ctx or not ctx.odds:
        return None

    best_offer: dict[str, object] | None = None
    for offer in ctx.odds:
        if not isinstance(offer, dict):
            continue

        market = str(offer.get("market") or "").lower()
        values = offer.get("values") or []
        if not isinstance(values, list):
            continue

        for value in values:
            if not isinstance(value, dict):
                continue
            label = str(value.get("value") or value.get("name") or value.get("label") or "").lower()
            odd = parse_odd(value.get("odd") or value.get("odds") or value.get("price"))
            if odd is None:
                continue
            if odds_value_matches_pick(pick, ctx, market, label):
                if best_offer is None or odd > float(best_offer["odd"]):
                    best_offer = {
                        "bookmaker_id": offer.get("bookmaker_id"),
                        "bookmaker": offer.get("bookmaker"),
                        "market": offer.get("market"),
                        "value": value.get("value") or value.get("name") or value.get("label"),
                        "odd": odd,
                    }

    return best_offer


def odds_value_matches_pick(pick: AiPick, ctx: CandidateContext, market: str, label: str) -> bool:
    """Сопоставить наш BetCode с названиями рынков API-Football."""
    code = pick.main_bet_code
    text = f"{market} {label}".lower()
    home = ctx.home_team.lower()
    away = ctx.away_team.lower()

    if code == "OVER_1_5":
        return "over" in label and ("1.5" in label or "1,5" in label)
    if code == "OVER_2_5":
        return "over" in label and ("2.5" in label or "2,5" in label)
    if code == "BTTS_YES":
        return ("both teams" in market or "btts" in market) and label in {"yes", "так"}
    if code == "HOME_DOUBLE_CHANCE":
        return "double chance" in market and ("home/draw" in label or "1x" in label or home in label)
    if code == "AWAY_DOUBLE_CHANCE":
        return "double chance" in market and ("draw/away" in label or "x2" in label or away in label)
    if code == "HOME_DNB":
        return ("draw no bet" in market or "dnb" in market) and (label == "home" or home in label)
    if code == "AWAY_DNB":
        return ("draw no bet" in market or "dnb" in market) and (label == "away" or away in label)

    return False


def parse_odd(raw: object) -> float | None:
    try:
        odd = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return odd if odd > 1 else None


def format_bookmaker_odds(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}"


def build_ggbet_match_url(home_team: str, away_team: str, start_time: str, tz_name: str = "Europe/Kiev") -> str:
    """Собрать match-level URL GGBET: /sports/match/home-vs-away-dd-mm."""
    slug = f"{slugify_url_part(home_team)}-vs-{slugify_url_part(away_team)}"
    match_date = parse_datetime_to_utc(str(start_time or ""))
    if match_date:
        try:
            match_date = match_date.astimezone(ZoneInfo(tz_name or "Europe/Kiev"))
        except Exception:
            match_date = match_date.astimezone(ZoneInfo("Europe/Kiev"))
        slug = f"{slug}-{match_date.strftime('%d-%m')}"

    return f"https://ggbet.ua/uk-ua/sports/match/{slug}"


def slugify_url_part(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\b(club|football|soccer)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    slug = re.sub(r"-+", "-", text).strip("-") or "team"
    return GGBET_TEAM_SLUG_ALIASES.get(slug, slug)


GGBET_TEAM_SLUG_ALIASES = {
    # GGBET keeps the Dutch club prefix for this team; API-Football usually does not.
    "ijsselmeervogels": "vv-ijsselmeervogels",
}


def extract_search_candidates(data: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in data.get("organic_results", []) or []:
        link = str(row.get("link") or "")
        if link:
            result.append(
                {
                    "link": link,
                    "title": str(row.get("title") or ""),
                    "snippet": str(row.get("snippet") or ""),
                }
            )
    return result


def split_match_title(title: str) -> tuple[str, str]:
    if "—" in title:
        left, right = title.split("—", 1); return left.strip(), right.strip()
    if "-" in title:
        left, right = title.split("-", 1); return left.strip(), right.strip()
    return title, ""


def safe_fixture_title(fixture: object) -> str:
    if hasattr(fixture, "home_team") and hasattr(fixture, "away_team"):
        return f"{fixture.home_team} — {fixture.away_team}"
    if isinstance(fixture, dict):
        teams = fixture.get("teams", {})
        return f"{teams.get('home', {}).get('name', 'Home')} — {teams.get('away', {}).get('name', 'Away')}"
    return str(fixture)[:100]
