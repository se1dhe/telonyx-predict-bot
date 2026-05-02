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
Ты футбольный аналитик для Telegram-бота.

Методология:
1. Форма команд за последние игры.
2. BTTS, Over 1.5, Over 2.5, забивают/пропускают.
3. H2H.
4. Таблица, рейтинг Elo, контекст турнира.
5. Новости/травмы/составы из SerpAPI. Не выдумывать.
6. В LOCAL режиме учитывать, что injuries/current odds могут быть неполными.
7. Отсеять сомнительные матчи.
8. Оставить максимум {self.settings.matches_per_day} лучших матчей.
9. Выбрать самый логичный рынок:
OVER_1_5, OVER_2_5, BTTS_YES, HOME_DOUBLE_CHANCE, AWAY_DOUBLE_CHANCE,
HOME_OR_DRAW_OVER_1_5, AWAY_OR_DRAW_OVER_1_5, HOME_DNB, AWAY_DNB, NO_BET.

Правила:
- Не выбирать ниже confidence {self.settings.min_ai_confidence}.
- Не писать гарантий.
- fixture_id брать строго из кандидата.
- tracking_url брать строго из кандидата.
- Вернуть только JSON без markdown.

Формат:
{{
 "selected": [{{
  "fixture_id": "id",
  "match_title": "Team A — Team B",
  "ai_rank_score": 80,
  "predicted_winner": "Team A не проиграет / исход опасный",
  "who_should_score": "Обе / Team A / осторожнее через тотал",
  "main_bet_code": "OVER_2_5",
  "main_bet_label": "ТБ 2.5",
  "safe_bet_label": "ТБ 1.5",
  "risky_bet_label": "ОЗ Да",
  "risk_level": "низкий / средний / высокий",
  "confidence": 70,
  "expected_score": "2:1",
  "why_this_match_is_gold": "Почему матч прошёл фильтр",
  "reasoning": "Короткий, но вдумчивый разбор",
  "data_warnings": ["нет подтверждённых травм"],
  "tracking_url": "https://www.sofascore.com/search?q=..."
 }}],
 "rejected_summary": ["Team C — Team D: мало данных"]
}}

Кандидаты:
{json.dumps(payload, ensure_ascii=False)}
"""
        response = await self.client.responses.create(model=self.settings.openai_model, input=prompt, temperature=0.18)
        raw = response.output_text.strip()
        logger.info("AI raw response length: %s", len(raw))
        parsed = AiSelectionResponse.model_validate(extract_json(raw))
        parsed.selected = [p for p in parsed.selected if p.main_bet_code != "NO_BET" and p.confidence >= self.settings.min_ai_confidence][:self.settings.matches_per_day]
        return parsed

def extract_json(text: str) -> dict:
    """Извлечь JSON из ответа модели."""
    if text.startswith("```"):
        text = text.strip("`").replace("json\n", "", 1).replace("JSON\n", "", 1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end >= 0:
        text = text[start:end+1]
    return json.loads(text)
