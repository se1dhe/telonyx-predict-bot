from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

# Маппинг наиболее частых систем PayKassa SCI.
# Для TelOnyx Predict сейчас нужен именно TRON_TRC20 / USDT.
PAYKASSA_SYSTEM_IDS = {
    "TRON_TRC20": "30",
    "USDT_TRC20": "30",
    "TRC20": "30",
}


class PayKassaClient:
    """SCI-клиент PayKassa для создания и подтверждения оплат.

    В v32 исправлено:
    - endpoint по умолчанию обновлён до /sci/0.4/index.php;
    - PAYKASSA_SYSTEM может быть как "30", так и "TRON_TRC20";
    - в create_order в PayKassa уходит числовой system id;
    - при 403/сетевой ошибке клиент пробует fallback endpoint;
    - IPN подтверждается через sci_confirm_order.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.endpoints = self._build_endpoints()

    async def create_order(self, amount: float, order_id: str, comment: str) -> str:
        """Создать счёт PayKassa и вернуть URL оплаты."""
        if not self.settings.paykassa_enabled:
            raise RuntimeError("PayKassa disabled")
        if not self.settings.paykassa_sci_id or not self.settings.paykassa_sci_key:
            raise RuntimeError("PayKassa SCI credentials are empty")

        payload = self._base_payload()
        payload.update(
            {
                "func": "sci_create_order",
                "amount": f"{amount:.2f}",
                "currency": self.settings.paykassa_currency.strip().upper(),
                "order_id": order_id,
                "comment": comment,
                "system": self._system_id(),
            }
        )

        data = await self._post(payload, operation="create_order")
        url = self._extract_payment_url(data)

        if not url:
            raise RuntimeError(f"PayKassa did not return payment URL. Parsed response: {data}")

        return url

    async def confirm_order(self, private_hash: str) -> dict:
        """Подтвердить IPN от PayKassa через sci_confirm_order."""
        if not private_hash:
            raise RuntimeError("private_hash is empty")

        payload = self._base_payload()
        payload.update(
            {
                "func": "sci_confirm_order",
                "private_hash": private_hash,
            }
        )

        return await self._post(payload, operation="confirm_order")

    def _base_payload(self) -> dict:
        """Общие поля SCI-запроса."""
        return {
            "sci_id": self.settings.paykassa_sci_id,
            "sci_key": self.settings.paykassa_sci_key,
            "domain": self._domain(),
            "test": "true" if self.settings.paykassa_test_mode else "false",
        }

    def _system_id(self) -> str:
        """Вернуть числовой ID платёжной системы для PayKassa SCI."""
        raw = str(self.settings.paykassa_system or "").strip()

        if raw.isdigit():
            return raw

        normalized = raw.upper().replace("-", "_").replace(" ", "_")
        mapped = PAYKASSA_SYSTEM_IDS.get(normalized)
        if mapped:
            return mapped

        # Безопасный дефолт для USDT TRC20.
        logger.warning(
            "Unknown PAYKASSA_SYSTEM=%s. Fallback to TRON_TRC20 system id 30.",
            raw,
        )
        return "30"

    def _build_endpoints(self) -> list[str]:
        """Собрать список endpoint'ов без дублей."""
        values: list[str] = []

        primary = str(getattr(self.settings, "paykassa_endpoint", "") or "").strip()
        if primary:
            values.append(primary)

        fallback_raw = str(getattr(self.settings, "paykassa_fallback_endpoints", "") or "").strip()
        if fallback_raw:
            for item in fallback_raw.split(","):
                item = item.strip()
                if item:
                    values.append(item)

        # Жёсткий fallback на случай, если env задан неправильно.
        values.extend(
            [
                "https://paykassa.pro/sci/0.4/index.php",
                "https://paykassa.app/sci/0.4/index.php",
            ]
        )

        result: list[str] = []
        seen = set()
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)

        return result

    async def _post(self, payload: dict, operation: str) -> dict:
        """Отправить запрос в PayKassa.

        Если первый endpoint вернул 403/5xx/сетевую ошибку — пробуем следующий.
        Если PayKassa вернул валидную бизнес-ошибку в JSON, fallback не скрывает её.
        """
        safe_payload = dict(payload)
        if "sci_key" in safe_payload:
            safe_payload["sci_key"] = "***"

        last_error: Exception | None = None

        for endpoint in self.endpoints:
            logger.info("PayKassa %s endpoint=%s payload=%s", operation, endpoint, safe_payload)

            try:
                raw_text, status = await self._post_once(endpoint, payload, operation)
                logger.info(
                    "PayKassa %s HTTP response: endpoint=%s status=%s body=%s",
                    operation,
                    endpoint,
                    status,
                    raw_text[:4000],
                )

                if status >= 400:
                    raise RuntimeError(
                        f"PayKassa HTTP {status} on {endpoint}: {raw_text[:1000]}"
                    )

                parsed = self._parse_response(raw_text)

                if isinstance(parsed, dict) and parsed.get("error"):
                    raise RuntimeError(parsed.get("message") or str(parsed))

                return parsed

            except RuntimeError as exc:
                last_error = exc
                message = str(exc)
                logger.warning("PayKassa %s failed on %s: %s", operation, endpoint, message)

                # Если это валидная бизнес-ошибка PayKassa, а не endpoint/HTTP проблема — дальше не прыгаем.
                if "PayKassa HTTP" not in message and "non-JSON" not in message and "empty response" not in message:
                    break

            except Exception as exc:
                last_error = exc
                logger.warning("PayKassa %s network/client error on %s: %s", operation, endpoint, exc)

        raise RuntimeError(
            f"PayKassa {operation} failed on all endpoints. Last error: {last_error}. "
            "Проверь Merchant ID/Password, домен predict.telonyx.app, test mode и доступность магазина."
        )

    async def _post_once(self, endpoint: str, payload: dict, operation: str) -> tuple[str, int]:
        timeout = aiohttp.ClientTimeout(total=25)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                data=payload,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (compatible; TelOnyxPredictBot/1.0; +https://predict.telonyx.app)",
                    "Origin": f"https://{self._domain()}",
                    "Referer": f"https://{self._domain()}/",
                },
            ) as response:
                return await response.text(), response.status

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

            for key in ("data[url]", "data[link]", "url", "link", "redirect_url", "payment_url"):
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

        compact = re.sub(r"\s+", " ", text)
        raise RuntimeError(f"PayKassa returned non-JSON response: {compact[:1000]}")

    def _extract_payment_url(self, data: dict) -> str:
        if not isinstance(data, dict):
            return ""

        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("url", "link", "payment_url", "redirect_url"):
                if nested.get(key):
                    return str(nested[key])

        for key in ("url", "link", "payment_url", "redirect_url"):
            if data.get(key):
                return str(data[key])

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
