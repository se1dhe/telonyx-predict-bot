from __future__ import annotations

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

        parsed = AiSelectionResponse.model_validate(extract_json(raw))
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
                    "AI PICK #%s | provider=%s | match=%s | bet=%s | confidence=%s/100 | risk=%s | expected_score=%s",
                    index,
                    self.provider,
                    pick.match_title,
                    pick.main_bet_label,
                    pick.confidence,
                    pick.risk_level,
                    pick.expected_score,
                )
                logger.info("AI PICK #%s | why=%s", index, pick.why_this_match_is_gold)
                logger.info("AI PICK #%s | reasoning=%s", index, pick.reasoning)
                logger.info("AI PICK #%s | winner=%s | scorer=%s", index, pick.predicted_winner, pick.who_should_score)

            for rejected in parsed.rejected_summary[:10]:
                logger.info("AI REJECTED | provider=%s | %s", self.provider, rejected)

        return parsed

    def _build_prompt(self, payload: list[dict]) -> str:
        """Единый промпт для OpenAI и Gemini."""
        return f"""
Ти футбольний аналітик для Telegram-бота TelOnyx Predict.

ВАЖЛИВО:
- Усі текстові поля в JSON відповіді пиши українською мовою.
- Не змішуй українську з російською.
- Не вигадуй травми, коефіцієнти, склади або новини, якщо їх немає в даних.
- Якщо даних недостатньо або матч сумнівний — краще відхилити.
- Відповідай тільки валідним JSON без markdown, без пояснень поза JSON.

Методологія:
1. Форма команд за останні матчі.
2. BTTS, Over 1.5, Over 2.5, забивають/пропускають.
3. H2H.
4. Таблиця, рейтинг Elo, турнірний контекст.
5. Новини/травми/склади із SerpAPI.
6. У LOCAL режимі враховуй, що injuries/current odds можуть бути неповними.
7. Відсій сумнівні матчі.
8. Залиши максимум {self.settings.matches_per_day} найкращих матчів.
9. Обери найлогічніший ринок:
OVER_1_5, OVER_2_5, BTTS_YES, HOME_DOUBLE_CHANCE, AWAY_DOUBLE_CHANCE,
HOME_OR_DRAW_OVER_1_5, AWAY_OR_DRAW_OVER_1_5, HOME_DNB, AWAY_DNB, NO_BET.

Вимоги:
- Не обирай нижче confidence {self.settings.min_ai_confidence}.
- Не давай агресивні ставки без сильних даних.
- Якщо матч сумнівний — відхиляй.
- Для risk_level використовуй тільки: низький, середній, високий.
- tracking_url і bookmaker_url бери з даних кандидата, якщо вони є.
- event_id бери з даних кандидата.

Формат відповіді:
{{
 "selected": [{{
  "match_title": "Team A — Team B",
  "event_id": "id",
  "main_bet_code": "OVER_1_5",
  "main_bet_label": "Тотал більше 1.5",
  "predicted_winner": "господарі ближче до перемоги / результат ризиковий",
  "who_should_score": "обидві можуть забити / краще через тотал",
  "safe_bet_label": "Тотал більше 1.5",
  "risky_bet_label": "Обидві заб’ють — так",
  "risk_level": "низький / середній / високий",
  "confidence": 70,
  "expected_score": "2:1",
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

    async def _call_openai(self, prompt: str) -> str:
        """Вызов OpenAI Responses API."""
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is empty")

        client = self.openai_client or AsyncOpenAI(api_key=self.settings.openai_api_key)

        request_kwargs = {
            "model": self.settings.openai_model,
            "input": prompt,
        }

        # Некоторые reasoning-модели OpenAI не поддерживают temperature.
        model_name = self.settings.openai_model.lower()
        if not model_name.startswith(("gpt-5", "o1", "o3", "o4")):
            request_kwargs["temperature"] = 0.18

        response = await client.responses.create(**request_kwargs)
        return response.output_text.strip()

    async def _call_gemini(self, prompt: str) -> str:
        """Вызов Google Gemini REST API без дополнительной зависимости."""
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is empty")

        model = (self.settings.gemini_model or "gemini-1.5-pro").strip()
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
                "temperature": 0.15,
                "topP": 0.8,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }

        timeout = aiohttp.ClientTimeout(total=90)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                json=body,
                headers={"Content-Type": "application/json"},
            ) as response:
                raw = await response.text()

                if response.status >= 400:
                    raise RuntimeError(f"Gemini HTTP {response.status}: {raw[:1500]}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini returned non-JSON HTTP body: {raw[:1500]}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")

        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )

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
