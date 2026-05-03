from __future__ import annotations
import logging
import re
from datetime import date, datetime, timezone, timedelta
from html import escape
from zoneinfo import ZoneInfo
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
from app.services.draftkings_resolver import DraftKingsResolver

logger = logging.getLogger(__name__)

class DailyPipeline:
    """Джерело даних → фільтри → новини → AI → база."""
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = get_data_provider()
        self.news = NewsSearchClient()
        self.ai = AiSelector()
        self.rule_based = RuleBasedSelector()
        self.draftkings = DraftKingsResolver()

    async def run_for_today(self, force: bool = False) -> tuple[dict[str, str], dict[str, list[str]]]:
        today = datetime.now(ZoneInfo(self.settings.tz)).date()
        return await self.run_for_date(today, force=force)

    async def run_for_date(self, target_date: date, force: bool = False) -> tuple[dict[str, str], dict[str, list[str]]]:
        date_key = target_date.isoformat()
        provider_name = self.settings.provider_normalized
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
        raw_fixtures = self._filter_raw_fixtures(raw_fixtures)
        debug.append(f"🔎 Після первинного фільтра: {len(raw_fixtures)}")
        contexts: list[CandidateContext] = []
        errors, rejected = [], []
        for fixture in raw_fixtures[:self.settings.max_raw_events]:
            try:
                ctx = await self.provider.build_context(fixture)
                if ctx.pre_ai_score < self.settings.min_context_pre_ai_score or ctx.data_quality_score < self.settings.min_context_data_quality:
                    rejected.append(f"{ctx.home_team} — {ctx.away_team}: data={ctx.data_quality_score}, pre_ai={ctx.pre_ai_score}, league={ctx.league_name}")
                    continue
                ctx.news = await self.news.search(ctx.home_team, ctx.away_team, ctx.league_name)
                contexts.append(ctx)
            except Exception as exc:
                errors.append(f"{safe_fixture_title(fixture)}: {str(exc)[:250]}")
                logger.exception("Помилка під час збору контексту матчу")
        contexts.sort(key=lambda c: c.pre_ai_score, reverse=True)
        contexts = contexts[:self.settings.max_candidates_for_ai]
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
                logger.info("Pipeline: отправляю %s кандидатов в OpenAI", len(contexts))
                ai_response = await self.ai.select_gold_matches(contexts)
                logger.info("Pipeline: OpenAI вернул выбранных матчей: %s", len(ai_response.selected))
            except Exception as exc:
                safe_error = escape(str(exc)[:1000])

                if self.settings.ai_fallback_on_error:
                    logger.exception("OpenAI не смог выбрать матчи, включаю rule-based fallback")
                    debug.append("🧠 OpenAI: ошибка, используется локальный fallback")
                    debug.append(f"⚠️ OpenAI error: <code>{safe_error}</code>")
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
            logger.info("Pipeline: OpenAI отключён, использую локальный rule-based selector")
            debug.append("🧠 OpenAI: отключён, используется локальный алгоритм")
            ai_response = await self.rule_based.select_gold_matches(contexts)

        picks = self._enrich_picks_with_context(ai_response.selected, contexts)
        await self._resolve_bookmaker_links(picks)
        self._log_selected_picks(picks)
        debug.append(f"✅ Вибрано матчів: {len(picks)}")
        if not picks:
            summary = "⚠️ AI не вибрав жодного матчу.\n\n<b>Діагностика:</b>\n" + "\n".join(debug)
            if ai_response.rejected_summary:
                summary += "\n\n<b>AI rejected:</b>\n" + "\n".join(f"• {escape(x)}" for x in ai_response.rejected_summary[:8])
            await self._save_daily_run(date_key, summary, 0)
            return self._no_quality_matches_result()
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}

        # Один анализ — несколько языковых рендеров.
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

        # Техническую диагностику не показываем пользователю, только в Railway Logs.
        logger.info("Техническая диагностика daily run:\n%s", "\n".join(debug))

        uk_summary = summaries.get("uk") or next(iter(summaries.values()))
        uk_details = details_by_lang.get("uk") or next(iter(details_by_lang.values()))
        await self._save_predictions(date_key, picks, uk_details, contexts, provider_name)
        await self._save_daily_run(date_key, uk_summary, len(picks))
        return summaries, details_by_lang


    def _single_language_result(self, summary: str, details: list[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Завернуть служебный одноязычный ответ в новый формат для всех активных языков."""
        return (
            {lang: summary for lang in self.settings.render_languages},
            {lang: details for lang in self.settings.render_languages},
        )

    def _no_quality_matches_result(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Языковой ответ, когда качественных матчей нет."""
        summaries = {
            lang: render_daily_summary([], [], provider=self.settings.provider_normalized, contexts_by_id={}, lang=lang)
            for lang in self.settings.render_languages
        }
        return summaries, {lang: [] for lang in self.settings.render_languages}

    def _split_pick_teams(self, pick: AiPick) -> tuple[str, str]:
        """Получить home/away из match_title."""
        if "—" in pick.match_title:
            home, away = pick.match_title.split("—", 1)
            return home.strip(), away.strip()
        if " vs " in pick.match_title.lower():
            parts = re.split(r"\s+vs\s+", pick.match_title, flags=re.IGNORECASE)
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()
        return pick.match_title.strip(), ""

    async def _resolve_bookmaker_links(self, picks: list[AiPick]) -> None:
        """Найти точные DraftKings event URLs для выбранных матчей."""
        if not self.settings.bookmaker_link_enabled:
            return

        for pick in picks:
            home, away = self._split_pick_teams(pick)
            if not home or not away:
                continue

            url = await self.draftkings.resolve(home, away)
            pick.bookmaker_url = url

            if url:
                logger.info("Bookmaker URL set for %s: %s", pick.match_title, url)
            else:
                logger.info("Bookmaker URL not found for %s", pick.match_title)

    def _log_selected_picks(self, picks: list[AiPick]) -> None:
        """Писать в Railway Logs reasoning/уверенность по выбранным матчум."""
        if not self.settings.log_ai_reasoning:
            return

        for index, pick in enumerate(picks, start=1):
            logger.info(
                "SELECTED PICK #%s | match=%s | bet=%s | confidence=%s/100 | risk=%s | score=%s | source=%s",
                index,
                pick.match_title,
                pick.main_bet_label,
                pick.confidence,
                pick.risk_level,
                pick.expected_score,
                getattr(pick, "pick_source", ""),
            )
            logger.info("SELECTED PICK #%s | why=%s", index, pick.why_this_match_is_gold)
            logger.info("SELECTED PICK #%s | reasoning=%s", index, pick.reasoning)

    def _enrich_picks_with_context(self, picks: list[AiPick], contexts: list[CandidateContext]) -> list[AiPick]:
        """Синхронизировать ссылку/дату/турнир прогноза с исходным контекстом."""
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}
        for pick in picks:
            ctx = ctx_by_id.get(pick.fixture_id)
            if not ctx:
                continue
            # AI обязан брать tracking_url из контекста, но дополнительно страхуемся.
            pick.tracking_url = ctx.tracking_url
        return picks

    def _filter_raw_fixtures(self, fixtures: list) -> list:
        """Первичный фильтр матчей.

        Главная защита v34:
        - не отдаём в прогнозы уже начавшиеся/прошедшие матчи;
        - оставляем только матчи, которые стартуют минимум через
          MIN_MATCH_START_LEAD_MINUTES минут;
        - LOCAL-источники тоже проходят этот фильтр, а не возвращаются «как есть».
        """
        allowed = self.settings.allowed_countries
        preferred = set(str(x) for x in self.settings.preferred_league_ids)
        result = []

        now_utc = datetime.now(timezone.utc)
        min_start_utc = now_utc + timedelta(minutes=max(0, self.settings.min_match_start_lead_minutes))

        for fixture in fixtures:
            if isinstance(fixture, dict):
                status_short = fixture.get("fixture", {}).get("status", {}).get("short") or ""
                league = fixture.get("league", {}) or {}
                country = str(league.get("country", ""))
                league_id = str(league.get("id", ""))
                league_name = str(league.get("name", ""))
                timestamp = fixture.get("fixture", {}).get("timestamp")
                start_utc = fixture_start_utc(fixture)
            else:
                status_value = getattr(fixture, "status", "")
                status_short = status_value.get("short") if isinstance(status_value, dict) else (status_value or "NS")
                country = str(getattr(fixture, "country", ""))
                league_id = str(getattr(fixture, "league_id", ""))
                league_name = str(getattr(fixture, "league_name", ""))
                timestamp = getattr(fixture, "timestamp", None)
                start_utc = fixture_start_utc(fixture)

            # Берём только запланированные матчи.
            if status_short and status_short not in {"NS", "TBD"}:
                continue

            # Если удалось определить старт — матч должен быть строго будущим.
            if start_utc is not None and start_utc < min_start_utc:
                logger.info(
                    "Fixture skipped because already started/past: %s start_utc=%s min_start_utc=%s",
                    safe_fixture_title(fixture),
                    start_utc.isoformat(),
                    min_start_utc.isoformat(),
                )
                continue

            if allowed and country and country.lower() not in allowed:
                continue

            if preferred and league_id and league_id not in preferred:
                continue

            text = f"{league_name} {country}".lower()
            if any(w in text for w in ["u17", "u19", "u20", "u21", "women", "amateur", "esoccer", "virtual", "friendly"]):
                continue

            result.append(fixture)

        result.sort(key=lambda f: fixture_sort_timestamp(f))
        return result[: self.settings.max_raw_events]


    async def _load_existing_texts(self, date_key: str, provider_name: str) -> tuple[dict[str, str], dict[str, list[str]]] | None:
        async with SessionLocal() as session:
            run = (await session.execute(select(DailyRun).where(DailyRun.date_key == date_key))).scalar_one_or_none()
            preds = (await session.execute(select(Prediction).where(Prediction.date_key == date_key).where(Prediction.provider == provider_name).order_by(Prediction.ai_rank_score.desc()))).scalars().all()
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
            for index, pick in enumerate(picks):
                rendered = details[index] if index < len(details) else ""
                existing = (await session.execute(select(Prediction).where(Prediction.date_key == date_key).where(Prediction.provider == provider_name).where(Prediction.fixture_id == pick.fixture_id))).scalar_one_or_none()
                home, away = split_match_title(pick.match_title)
                ctx = ctx_by_id.get(pick.fixture_id)
                if existing:
                    existing.prediction_json = pick.model_dump_json(); existing.rendered_text = rendered
                    existing.main_bet_code = pick.main_bet_code; existing.main_bet_label = pick.main_bet_label
                    existing.confidence = pick.confidence; existing.ai_rank_score = pick.ai_rank_score
                else:
                    session.add(Prediction(date_key=date_key, provider=provider_name, fixture_id=pick.fixture_id,
                        home_team=home, away_team=away, league_name=ctx.league_name if ctx else "",
                        country=ctx.country if ctx else "", start_time=ctx.start_time if ctx else "",
                        source_league_code=ctx.source_league_code if ctx else "", prediction_json=pick.model_dump_json(),
                        rendered_text=rendered, main_bet_code=pick.main_bet_code, main_bet_label=pick.main_bet_label,
                        confidence=pick.confidence, ai_rank_score=pick.ai_rank_score))
            await session.commit()


def fixture_start_utc(fixture: object) -> datetime | None:
    """Определить старт матча в UTC.

    Для LOCAL date+time считаем UTC, потому что TheSportsDB/ESPN обычно
    возвращают UTC-время, а рендер уже переводит его в Киев.
    """
    try:
        if isinstance(fixture, dict):
            timestamp = fixture.get("fixture", {}).get("timestamp")
            if timestamp:
                return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)

            raw_date = fixture.get("fixture", {}).get("date") or ""
            if raw_date:
                dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)

        fixture_date = getattr(fixture, "date", None)
        fixture_time = str(getattr(fixture, "time", "") or "").strip()

        if fixture_date:
            if fixture_time and len(fixture_time) >= 5:
                hh, mm = fixture_time[:5].split(":", 1)
                return datetime(
                    fixture_date.year,
                    fixture_date.month,
                    fixture_date.day,
                    int(hh),
                    int(mm),
                    tzinfo=timezone.utc,
                )

            # Если времени нет, считаем старт концом дня UTC, чтобы матч не отсеять ошибочно утром.
            return datetime(
                fixture_date.year,
                fixture_date.month,
                fixture_date.day,
                23,
                59,
                tzinfo=timezone.utc,
            )
    except Exception:
        return None

    return None



def fixture_sort_key(fixture: object) -> int:
    """Обратная совместимость со старым именем сортировки."""
    return fixture_sort_timestamp(fixture)

def fixture_sort_timestamp(fixture: object) -> int:
    start = fixture_start_utc(fixture)
    if start is not None:
        return int(start.timestamp())

    if isinstance(fixture, dict):
        return int(fixture.get("fixture", {}).get("timestamp") or 9999999999)

    return int(getattr(fixture, "timestamp", None) or 9999999999)

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
