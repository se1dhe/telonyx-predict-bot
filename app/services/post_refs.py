from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostRef:
    """Ссылка на уже опубликованный Telegram-пост."""

    lang: str
    chat_id: str
    message_id: int
    kind: str = "private"


def dumps_refs(refs: list[PostRef]) -> str:
    """Сериализовать refs в JSON для хранения в predictions."""
    if not refs:
        return ""
    return json.dumps([ref.__dict__ for ref in refs], ensure_ascii=False)


def loads_refs(raw: str | None) -> list[PostRef]:
    """Прочитать refs из JSON. Битый JSON не должен ронять scheduler."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        result = []
        for item in data if isinstance(data, list) else []:
            result.append(
                PostRef(
                    lang=str(item.get("lang") or "uk"),
                    chat_id=str(item.get("chat_id") or ""),
                    message_id=int(item.get("message_id") or 0),
                    kind=str(item.get("kind") or "private"),
                )
            )
        return [ref for ref in result if ref.chat_id and ref.message_id > 0]
    except Exception:
        logger.exception("Failed to parse Telegram post refs")
        return []
