from __future__ import annotations

import uuid
from urllib.parse import urlencode
import aiohttp

from app.config import get_settings


class PayKassaClient:
    """Минимальный SCI-клиент PayKassa.

    Для создания ссылки используется SCI endpoint. Точные значения system/currency
    настраиваются в Railway, потому что в кабинете PayKassa они зависят от активированных методов.
    """

    endpoint = "https://paykassa.pro/sci/0.3/index.php"

    def __init__(self) -> None:
        self.settings = get_settings()

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
            "system": self.settings.paykassa_system,
            "sci_id": self.settings.paykassa_sci_id,
            "sci_key": self.settings.paykassa_sci_key,
            "domain": self.settings.project_public_url.replace("https://", "").replace("http://", "").strip("/"),
            "test": str(self.settings.paykassa_test_mode).lower(),
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.endpoint, data=payload) as response:
                data = await response.json(content_type=None)

        if data.get("error"):
            raise RuntimeError(data.get("message") or str(data))

        url = data.get("data", {}).get("url")
        if not url:
            raise RuntimeError(f"PayKassa did not return payment URL: {data}")

        return url

    async def confirm_order(self, private_hash: str) -> dict:
        payload = {
            "func": "sci_confirm_order",
            "private_hash": private_hash,
            "sci_id": self.settings.paykassa_sci_id,
            "sci_key": self.settings.paykassa_sci_key,
            "test": str(self.settings.paykassa_test_mode).lower(),
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.endpoint, data=payload) as response:
                data = await response.json(content_type=None)

        if data.get("error"):
            raise RuntimeError(data.get("message") or str(data))

        return data
