from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


class PayKassaClient:
    """Минимальный SCI-клиент PayKassa.

    Важно:
    PayKassa иногда возвращает не JSON, а HTML/текст ошибки, если:
    - SCI_ID неверный или пустой;
    - Merchant Password неверный;
    - домен ещё не подтверждён;
    - магазин не активирован;
    - выбранная система/валюта недоступна;
    - test mode не совпадает с настройками магазина.

    Поэтому мы сначала читаем raw text, логируем его, а потом уже пытаемся распарсить.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.endpoint = self.settings.paykassa_endpoint

    async def create_order(self, amount: float, order_id: str, comment: str) -> str:
        if not self.settings.paykassa_enabled:
            raise RuntimeError("PayKassa disabled")
        if not self.settings.paykassa_sci_id or not self.settings.paykassa_sci_key:
            raise RuntimeError("PayKassa SCI credentials are empty")

        payload = {
            "func": "sci_create_order",
            "amount": f"{amount:.2f}",
            "currency": self.settings.paykassa_currency,
            "order_id": order_id,
            "comment": comment,
            "system": self.settings.paykassa_system.strip().upper(),
            "sci_id": self.settings.paykassa_sci_id,
            "sci_key": self.settings.paykassa_sci_key,
            "domain": self._domain(),
            "test": "true" if self.settings.paykassa_test_mode else "false",
        }

        data = await self._post(payload, operation="create_order")
        url = self._extract_payment_url(data)

        if not url:
            raise RuntimeError(f"PayKassa did not return payment URL. Parsed response: {data}")

        return url

    async def confirm_order(self, private_hash: str) -> dict:
        payload = {
            "func": "sci_confirm_order",
            "private_hash": private_hash,
            "sci_id": self.settings.paykassa_sci_id,
            "sci_key": self.settings.paykassa_sci_key,
            "test": "true" if self.settings.paykassa_test_mode else "false",
        }

        return await self._post(payload, operation="confirm_order")

    async def _post(self, payload: dict, operation: str) -> dict:
        safe_payload = dict(payload)
        if "sci_key" in safe_payload:
            safe_payload["sci_key"] = "***"

        logger.info("PayKassa %s request payload: %s", operation, safe_payload)

        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.endpoint,
                data=payload,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (compatible; TelOnyxPredictBot/1.0; +https://predict.telonyx.app)",
                    "Origin": "https://predict.telonyx.app",
                    "Referer": "https://predict.telonyx.app/",
                },
            ) as response:
                raw_text = await response.text()

        logger.info("PayKassa %s HTTP response: status=%s body=%s", operation, response.status, raw_text[:4000])

        if response.status >= 400:
            raise RuntimeError(
                f"PayKassa HTTP {response.status}: {raw_text[:1000]} | "
                "Если это 403 nginx, проверь: активирован ли магазин, совпадает ли test mode, "
                "верный ли Merchant ID/Password, разрешён ли домен predict.telonyx.app, "
                "и попробуй PAYKASSA_SYSTEM=TRON_TRC20."
            )

        parsed = self._parse_response(raw_text)

        # Формат JSON: {"error":true/false,"message":"...","data":{...}}
        if isinstance(parsed, dict) and parsed.get("error"):
            raise RuntimeError(parsed.get("message") or str(parsed))

        return parsed

    def _parse_response(self, raw_text: str) -> dict:
        text = (raw_text or "").strip()

        if not text:
            raise RuntimeError("PayKassa returned empty response")

        # JSON
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
            return {"data": value}
        except json.JSONDecodeError:
            pass

        # URL-encoded response: error=false&data[url]=...
        if "=" in text and "&" in text:
            qs = parse_qs(text, keep_blank_values=True)
            result: dict = {}
            for key, values in qs.items():
                result[key] = values[0] if values else ""

            # Нормализуем data[url]
            for key in ("data[url]", "url", "redirect_url", "payment_url"):
                if key in result:
                    result.setdefault("data", {})
                    if isinstance(result["data"], dict):
                        result["data"]["url"] = result[key]

            if str(result.get("error", "")).lower() in {"1", "true", "yes"}:
                result["error"] = True
            elif "error" in result:
                result["error"] = False

            return result

        # Иногда может прийти чистая ссылка.
        if text.startswith("http://") or text.startswith("https://"):
            return {"data": {"url": text}}

        # HTML/прочий текст — это неуспешный ответ.
        compact = re.sub(r"\s+", " ", text)
        raise RuntimeError(f"PayKassa returned non-JSON response: {compact[:1000]}")

    def _extract_payment_url(self, data: dict) -> str:
        if not isinstance(data, dict):
            return ""

        # Основной JSON-формат.
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("url", "payment_url", "redirect_url"):
                if nested.get(key):
                    return str(nested[key])

        # Плоский формат.
        for key in ("url", "payment_url", "redirect_url"):
            if data.get(key):
                return str(data[key])

        # URL мог лежать в message.
        message = str(data.get("message", ""))
        if message.startswith("http://") or message.startswith("https://"):
            return message

        return ""

    def _domain(self) -> str:
        value = self.settings.project_public_url.strip()
        parsed = urlparse(value)
        if parsed.netloc:
            return parsed.netloc
        return value.replace("https://", "").replace("http://", "").strip("/")
