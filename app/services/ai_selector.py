from __future__ import annotations
import json, logging
from openai import AsyncOpenAI
from app.config import get_settings
from app.schemas import AiSelectionResponse, CandidateContext

logger = logging.getLogger(__name__)

class AiSelector:
    """AI выбирает лучшие матчи."""
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    async def select_gold_matches(self, contexts: list[CandidateContext]) -> AiSelectionResponse:
        """Отправить кандидатов в OpenAI."""
        payload = []
        for ctx in contexts:
            item = ctx.model_dump(mode="json")
            item["tracking_url"] = ctx.tracking_url
            payload.append(item)
        prompt = f"""
Ти футбольний аналітик для Telegram-бота. Усі текстові поля в JSON відповіді пиши українською мовою.

Методологія:
1. Форма команд за останні матчі.
2. BTTS, Over 1.5, Over 2.5, забивають/пропускають.
3. H2H.
4. Таблиця, рейтинг Elo, турнірний контекст.
5. Новини/травми/склади із SerpAPI. Не вигадуй факти.
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
- Не вигадуй травми, коефіцієнти або новини, яких немає в даних.
- Відповідай тільки валідним JSON без markdown.

Формат:
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
        # Важно:
        # Некоторые reasoning-модели OpenAI не поддерживают temperature.
        # Поэтому temperature передаём только для моделей, где это безопасно.
        request_kwargs = {
            "model": self.settings.openai_model,
            "input": prompt,
        }

        model_name = self.settings.openai_model.lower()
        if not model_name.startswith(("gpt-5", "o1", "o3", "o4")):
            request_kwargs["temperature"] = 0.18

        response = await self.client.responses.create(**request_kwargs)
        raw = response.output_text.strip()
        logger.info("AI raw response length: %s", len(raw))

        if self.settings.log_ai_reasoning:
            logger.info("LOG_AI_REASONING=true; AI raw preview: %s", raw[:2500])

        parsed = AiSelectionResponse.model_validate(extract_json(raw))
        parsed.selected = [
            p for p in parsed.selected
            if p.main_bet_code != "NO_BET" and p.confidence >= self.settings.min_ai_confidence
        ][:self.settings.matches_per_day]

        if self.settings.log_ai_reasoning:
            for index, pick in enumerate(parsed.selected, start=1):
                logger.info(
                    "AI PICK #%s | match=%s | bet=%s | confidence=%s/100 | risk=%s | expected_score=%s",
                    index,
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
                logger.info("AI REJECTED | %s", rejected)

        return parsed

def extract_json(text: str) -> dict:
    """Извлечь JSON из ответа модели."""
    if text.startswith("```"):
        text = text.strip("`").replace("json\n", "", 1).replace("JSON\n", "", 1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end >= 0:
        text = text[start:end+1]
    return json.loads(text)
