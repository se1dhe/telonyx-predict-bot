from __future__ import annotations
import logging
from datetime import date, datetime
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

logger = logging.getLogger(__name__)

class DailyPipeline:
    """Источник данных → фильтры → новости → AI → база."""
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = get_data_provider()
        self.news = NewsSearchClient()
        self.ai = AiSelector()
        self.rule_based = RuleBasedSelector()

    async def run_for_today(self, force: bool = False) -> tuple[str, list[str]]:
        today = datetime.now(ZoneInfo(self.settings.tz)).date()
        return await self.run_for_date(today, force=force)

    async def run_for_date(self, target_date: date, force: bool = False) -> tuple[str, list[str]]:
        date_key = target_date.isoformat()
        provider_name = self.settings.provider_normalized
        if not force:
            cached = await self._load_existing_texts(date_key, provider_name)
            if cached:
                return cached
        debug = [f"📅 Дата: {date_key}", f"🧩 Источник: {provider_name}"]
        try:
            logger.info("Pipeline: получаю матчи от источника %s на дату %s", provider_name, date_key)
            raw_fixtures = await self.provider.fixtures_by_date(target_date)
            logger.info("Pipeline: источник вернул матчей: %s", len(raw_fixtures))
        except Exception as exc:
            summary = f"⚠️ Не удалось получить матчи из источника {provider_name}.\n\nОшибка: <code>{escape(str(exc)[:1000])}</code>"
            await self._save_daily_run(date_key, summary, 0)
            return summary, []
        debug.append(f"📦 Матчей от источника: {len(raw_fixtures)}")
        raw_fixtures = self._filter_raw_fixtures(raw_fixtures)
        debug.append(f"🔎 После первичного фильтра: {len(raw_fixtures)}")
        contexts: list[CandidateContext] = []
        errors, rejected = [], []
        for fixture in raw_fixtures[:self.settings.max_raw_events]:
            try:
                ctx = await self.provider.build_context(fixture)
                if ctx.pre_ai_score < 25 or ctx.data_quality_score < 20:
                    rejected.append(f"{ctx.home_team} — {ctx.away_team}: data={ctx.data_quality_score}, pre_ai={ctx.pre_ai_score}, league={ctx.league_name}")
                    continue
                ctx.news = await self.news.search(ctx.home_team, ctx.away_team, ctx.league_name)
                contexts.append(ctx)
            except Exception as exc:
                errors.append(f"{safe_fixture_title(fixture)}: {str(exc)[:250]}")
                logger.exception("Ошибка при сборе контекста матча")
        contexts.sort(key=lambda c: c.pre_ai_score, reverse=True)
        contexts = contexts[:self.settings.max_candidates_for_ai]
        debug += [f"🧠 Контекстов собрано: {len(contexts)}", f"🗑 Отсеяно по score: {len(rejected)}", f"❌ Ошибок при сборе: {len(errors)}"]
        if errors:
            debug.append("\n<b>Первые ошибки:</b>")
            debug += [f"• {escape(x)}" for x in errors[:5]]
        if rejected:
            debug.append("\n<b>Первые отсеянные:</b>")
            debug += [f"• {escape(x)}" for x in rejected[:5]]
        if not contexts:
            summary = "⚠️ На сегодня не найдено достаточно качественных матчей.\n\n<b>Диагностика:</b>\n" + "\n".join(debug)
            await self._save_daily_run(date_key, summary, 0)
            return summary, []
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
                        "⚠️ AI не смог выбрать матчи.\n\n"
                        "<b>Диагностика:</b>\n"
                        + "\n".join(debug)
                        + f"\n\n<b>Ошибка AI:</b>\n<code>{safe_error}</code>"
                    )
                    await self._save_daily_run(date_key, summary, 0)
                    return summary, []
        else:
            logger.info("Pipeline: OpenAI отключён, использую локальный rule-based selector")
            debug.append("🧠 OpenAI: отключён, используется локальный алгоритм")
            ai_response = await self.rule_based.select_gold_matches(contexts)

        picks = self._enrich_picks_with_context(ai_response.selected, contexts)
        self._log_selected_picks(picks)
        debug.append(f"✅ Выбрано матчей: {len(picks)}")
        if not picks:
            summary = "⚠️ AI не выбрал ни одного матча.\n\n<b>Диагностика:</b>\n" + "\n".join(debug)
            if ai_response.rejected_summary:
                summary += "\n\n<b>Что AI отклонил:</b>\n" + "\n".join(f"• {escape(x)}" for x in ai_response.rejected_summary[:8])
            await self._save_daily_run(date_key, summary, 0)
            return summary, []
        ctx_by_id = {ctx.fixture_id: ctx for ctx in contexts}
        summary = render_daily_summary(
            picks,
            ai_response.rejected_summary,
            provider=provider_name,
            contexts_by_id=ctx_by_id,
        )

        # Техническую диагностику не показываем пользователю, только в Railway Logs.
        logger.info("Техническая диагностика daily run:\n%s", "\n".join(debug))

        if self.settings.show_tech_diagnostics:
            summary += "\n\n<b>Техническая диагностика:</b>\n" + "\n".join(debug)

        details = [render_pick_detail(p, ctx_by_id.get(p.fixture_id)) for p in picks]
        await self._save_predictions(date_key, picks, details, contexts, provider_name)
        await self._save_daily_run(date_key, summary, len(picks))
        return summary, details

    def _log_selected_picks(self, picks: list[AiPick]) -> None:
        """Писать в Railway Logs reasoning/уверенность по выбранным матчам."""
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

        Поддерживает оба формата:
        - LOCAL dataclass / RawFixture-like object;
        - старый API-FOOTBALL dict.
        """
        if self.settings.provider_normalized == "LOCAL":
            return fixtures[: self.settings.max_raw_events]

        allowed = self.settings.allowed_countries
        preferred = set(str(x) for x in self.settings.preferred_league_ids)
        result = []

        for fixture in fixtures:
            if isinstance(fixture, dict):
                status_short = fixture.get("fixture", {}).get("status", {}).get("short")
                league = fixture.get("league", {})
                country = str(league.get("country", ""))
                league_id = str(league.get("id", ""))
                league_name = str(league.get("name", ""))
                timestamp = fixture.get("fixture", {}).get("timestamp", 9999999999)
            else:
                status_short = getattr(fixture, "status", {}).get("short") if isinstance(getattr(fixture, "status", {}), dict) else ""
                country = str(getattr(fixture, "country", ""))
                league_id = str(getattr(fixture, "league_id", ""))
                league_name = str(getattr(fixture, "league_name", ""))
                timestamp = getattr(fixture, "timestamp", 9999999999) or 9999999999

            if status_short not in {"NS", "TBD"}:
                continue

            if allowed and country.lower() not in allowed:
                continue

            if preferred and league_id not in preferred:
                continue

            text = f"{league_name} {country}".lower()
            if any(w in text for w in ["u17", "u19", "u20", "u21", "women", "amateur", "esoccer", "virtual", "friendly"]):
                continue

            result.append(fixture)

        result.sort(key=lambda f: f.get("fixture", {}).get("timestamp", 9999999999) if isinstance(f, dict) else (getattr(f, "timestamp", 9999999999) or 9999999999))
        return result[: self.settings.max_raw_events]

    async def _load_existing_texts(self, date_key: str, provider_name: str) -> tuple[str, list[str]] | None:
        async with SessionLocal() as session:
            run = (await session.execute(select(DailyRun).where(DailyRun.date_key == date_key))).scalar_one_or_none()
            preds = (await session.execute(select(Prediction).where(Prediction.date_key == date_key).where(Prediction.provider == provider_name).order_by(Prediction.ai_rank_score.desc()))).scalars().all()
        if run and preds:
            return run.summary_text, [p.rendered_text for p in preds]
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
