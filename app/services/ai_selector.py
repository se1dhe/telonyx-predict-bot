from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from openai import AsyncOpenAI

from app.config import get_settings
from app.schemas import AiSelectionResponse, CandidateContext

logger = logging.getLogger(__name__)


class AiSelector:
    """AI выбирает лучшие матчи.

    Поддерживаются провайдеры:
    - AI_PROVIDER="openai"
    - AI_PROVIDER="gemini"
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = (self.settings.ai_provider or "openai").strip().lower()

        self.openai_client: AsyncOpenAI | None = None
        if self.provider == "openai":
            self.openai_client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    async def select_gold_matches(self, contexts: list[CandidateContext]) -> AiSelectionResponse:
        """Отправить кандидатов в выбранную AI-модель."""
        payload = []
        for ctx in contexts:
            item = ctx.model_dump(mode="json")
            item["tracking_url"] = ctx.tracking_url
            payload.append(item)

        prompt = self._build_prompt(payload)

        if self.provider == "gemini":
            raw = await self._call_gemini(prompt)
        else:
            raw = await self._call_openai(prompt)

        logger.info("AI provider=%s raw response length: %s", self.provider, len(raw))

        if self.settings.log_ai_reasoning:
            logger.info("LOG_AI_REASONING=true; AI provider=%s raw preview: %s", self.provider, raw[:2500])

        try:
            parsed = self._parse_response(raw)
        except Exception as exc:
            if self.provider != "gemini":
                raise

            logger.warning(
                "Gemini returned malformed JSON, trying one repair request. error=%s raw_preview=%s",
                exc,
                raw[:1500],
            )
            repaired_raw = await self._call_gemini(self._build_json_repair_prompt(raw, str(exc)))

            if self.settings.log_ai_reasoning:
                logger.info("Gemini repaired JSON preview: %s", repaired_raw[:2500])

            parsed = self._parse_response(repaired_raw)

        parsed.selected = [
            p for p in parsed.selected
            if p.main_bet_code != "NO_BET" and p.confidence >= self.settings.min_ai_confidence
        ][:self.settings.matches_per_day]

        if self.settings.log_ai_reasoning:
            logger.info(
                "AI provider=%s selected=%s rejected=%s min_confidence=%s",
                self.provider,
                len(parsed.selected),
                len(parsed.rejected_summary),
                self.settings.min_ai_confidence,
            )

            for index, pick in enumerate(parsed.selected, start=1):
                logger.info(
                    "AI PICK #%s | provider=%s | match=%s | bet=%s | confidence=%s/100 | risk=%s",
                    index,
                    self.provider,
                    pick.match_title,
                    pick.main_bet_label,
                    pick.confidence,
                    pick.risk_level,
                )
                logger.info("AI PICK #%s | why=%s", index, pick.why_this_match_is_gold)
                logger.info("AI PICK #%s | reasoning=%s", index, pick.reasoning)
                logger.info("AI PICK #%s | winner=%s | scorer=%s", index, pick.predicted_winner, pick.who_should_score)

            for rejected in parsed.rejected_summary[:10]:
                logger.info("AI REJECTED | provider=%s | %s", self.provider, rejected)

        return parsed

    def _parse_response(self, raw: str) -> AiSelectionResponse:
        """Распарсить и провалидировать JSON-ответ AI."""
        return AiSelectionResponse.model_validate(extract_json(raw))

    def _build_prompt(self, payload: list[dict]) -> str:
        """Единый промпт для OpenAI и Gemini без прогнозов точного счёта."""
        return f"""
Ти футбольний аналітик для Telegram-бота TelOnyx Predict.

ВАЖЛИВО:
- Усі текстові поля в JSON відповіді пиши українською мовою.
- Не змішуй українську з російською.
- Не вигадуй травми, коефіцієнти, склади або новини, якщо їх немає в даних.
- Не прогнозуй точний або очікуваний рахунок.
- Не пропонуй ставки на точний рахунок / correct score / exact score.
- Якщо даних недостатньо або матч сумнівний — краще відхилити.
- Відповідай тільки валідним JSON без markdown, без пояснень поза JSON.
- JSON має бути повністю завершеним: закрий усі лапки, масиви й об’єкти.
- Не додавай текст поза JSON.

Методологія:
1. Форма команд за останні матчі.
2. BTTS, Over 1.5, Over 2.5, забивають/пропускають.
3. H2H.
4. Таблиця, рейтинг Elo, турнірний контекст.
5. Новини/травми/склади із SerpAPI.
6. У LOCAL режимі враховуй, що injuries/current odds можуть бути неповними.
7. Відсій сумнівні матчі.
8. Залиши максимум {self.settings.matches_per_day} найкращих матчів.
9. Обери тільки один із дозволених ринків:
OVER_1_5, OVER_2_5, BTTS_YES, HOME_DOUBLE_CHANCE, AWAY_DOUBLE_CHANCE,
HOME_OR_DRAW_OVER_1_5, AWAY_OR_DRAW_OVER_1_5, HOME_DNB, AWAY_DNB, NO_BET.

Заборонено:
- CORRECT_SCORE;
- EXACT_SCORE;
- SCORE;
- будь-який main_bet_label про точний рахунок.

Вимоги:
- Не обирай нижче confidence {self.settings.min_ai_confidence}.
- Не давай агресивні ставки без сильних даних.
- Якщо матч сумнівний — відхиляй.
- Для risk_level використовуй тільки: низький, середній, високий.
- tracking_url і bookmaker_url бери з даних кандидата, якщо вони є.
- fixture_id бери з даних кандидата.
- Поле expected_score не заповнюй, залишай порожнім рядком.

Формат відповіді:
{{
 "selected": [{{
  "fixture_id": "id",
  "match_title": "Team A — Team B",
  "ai_rank_score": 80,
  "main_bet_code": "OVER_1_5",
  "main_bet_label": "Тотал більше 1.5",
  "predicted_winner": "господарі ближче до перемоги / результат ризиковий",
  "who_should_score": "обидві можуть забити / краще через тотал",
  "safe_bet_label": "Тотал більше 1.5",
  "risky_bet_label": "Обидві заб’ють — так",
  "risk_level": "низький / середній / високий",
  "confidence": 70,
  "expected_score": "",
  "why_this_match_is_gold": "Чому матч пройшов фільтр",
  "reasoning": "Короткий, але вдумливий розбір",
  "data_warnings": ["немає підтверджених травм"],
  "tracking_url": "https://www.sofascore.com/search?q=...",
  "bookmaker_url": ""
 }}],
 "rejected_summary": ["Team C — Team D: мало даних"]
}}

Кандидати:
{json.dumps(payload, ensure_ascii=False)}
"""

    def _build_json_repair_prompt(self, raw: str, error: str) -> str:
        """Промпт для починки невалидного JSON от Gemini."""
        return f"""
Ти отримав невалідний JSON після футбольного аналізу.

Завдання:
- Виправ тільки JSON-синтаксис.
- Не додавай markdown.
- Не додавай пояснення.
- Не вигадуй нові матчі або нові дані.
- Якщо частина JSON була обрізана, закрий поточний об’єкт/масив коректно.
- Поверни тільки JSON у схемі:
{{"selected": [], "rejected_summary": []}}

Помилка парсингу:
{error[:500]}

Невалідний JSON:
{raw[:6000]}
"""

    async def _call_openai(self, prompt: str) -> str:
        """Вызов OpenAI Responses API."""
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is empty")

        client = self.openai_client or AsyncOpenAI(api_key=self.settings.openai_api_key)

        request_kwargs = {
            "model": self.settings.openai_model,
            "input": prompt,
        }

        model_name = self.settings.openai_model.lower()
        if not model_name.startswith(("gpt-5", "o1", "o3", "o4")):
            request_kwargs["temperature"] = 0.18

        response = await client.responses.create(**request_kwargs)
        return response.output_text.strip()

    async def _call_gemini(self, prompt: str) -> str:
        """Вызов Google Gemini REST API с retry и fallback-моделью."""
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is empty")

        primary_model = (self.settings.gemini_model or "gemini-2.5-flash").strip()
        fallback_model = (self.settings.gemini_fallback_model or "").strip()

        models = [primary_model]
        if fallback_model and fallback_model != primary_model:
            models.append(fallback_model)

        last_error: Exception | None = None

        for model_index, model in enumerate(models):
            attempts = max(1, int(self.settings.ai_retry_max_attempts or 1))

            for attempt in range(1, attempts + 1):
                try:
                    logger.info(
                        "Gemini request: model=%s attempt=%s/%s fallback=%s",
                        model,
                        attempt,
                        attempts,
                        model_index > 0,
                    )
                    return await self._call_gemini_once(prompt, model)

                except GeminiRateLimitError as exc:
                    last_error = exc
                    retry_after = exc.retry_after_seconds
                    fallback_available = model_index + 1 < len(models)
                    should_retry_same_model = attempt < attempts

                    if not should_retry_same_model and fallback_available:
                        logger.warning(
                            "Gemini model=%s exhausted by rate limit, switching to fallback model=%s",
                            model,
                            models[model_index + 1],
                        )
                        break

                    if not should_retry_same_model:
                        logger.warning("Gemini model=%s exhausted by rate limit and no fallback is available", model)
                        break

                    delay = retry_after or float(self.settings.ai_retry_base_delay_seconds or 8.0) * attempt
                    delay = max(1.0, min(delay, 45.0))
                    logger.warning(
                        "Gemini HTTP 429 for model=%s attempt=%s/%s; retry in %.1fs",
                        model,
                        attempt,
                        attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)

                except Exception as exc:
                    last_error = exc
                    if attempt >= attempts:
                        logger.warning("Gemini model=%s failed after %s attempts: %s", model, attempts, exc)
                        break

                    delay = float(self.settings.ai_retry_base_delay_seconds or 8.0) * attempt
                    delay = max(1.0, min(delay, 30.0))
                    logger.warning(
                        "Gemini error for model=%s attempt=%s/%s: %s; retry in %.1fs",
                        model,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(f"Gemini failed for all configured models: {last_error}")

    async def _call_gemini_once(self, prompt: str, model: str) -> str:
        """Один HTTP-запрос к Gemini без повторов."""
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.settings.gemini_api_key}"
        )

        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "topP": 0.8,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }

        timeout = aiohttp.ClientTimeout(total=max(20, int(self.settings.ai_timeout_seconds or 90)))

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                json=body,
                headers={"Content-Type": "application/json"},
            ) as response:
                raw = await response.text()

                if response.status == 429:
                    raise GeminiRateLimitError(raw[:1500], retry_after_seconds=parse_retry_delay(raw))

                if response.status >= 400:
                    raise RuntimeError(f"Gemini HTTP {response.status}: {raw[:1500]}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini returned non-JSON HTTP body: {raw[:1500]}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")

        candidate = candidates[0]
        finish_reason = str(candidate.get("finishReason") or "").upper()
        if finish_reason == "MAX_TOKENS":
            raise RuntimeError("Gemini response was truncated by MAX_TOKENS")

        parts = candidate.get("content", {}).get("parts", [])
        text_chunks = []

        for part in parts:
            if isinstance(part, dict) and part.get("text"):
                text_chunks.append(str(part["text"]))

        text = "\n".join(text_chunks).strip()
        if not text:
            raise RuntimeError(f"Gemini returned empty text: {data}")

        return text


def extract_json(text: str) -> dict:
    """Извлечь JSON из ответа модели."""
    text = (text or "").strip()

    if text.startswith("```"):
        text = text.strip("`").replace("json\n", "", 1).replace("JSON\n", "", 1).strip()

    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end >= 0:
        text = text[start:end + 1]

    return json.loads(text)


class GeminiRateLimitError(RuntimeError):
    """Gemini вернул HTTP 429 / RESOURCE_EXHAUSTED."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(f"Gemini HTTP 429: {message}")
        self.retry_after_seconds = retry_after_seconds


def parse_retry_delay(raw: str) -> float | None:
    """Достать retry delay из текста Gemini, например: 'Please retry in 23.361s'."""
    import re

    match = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", raw, flags=re.IGNORECASE)
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None
