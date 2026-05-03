from __future__ import annotations

from app.config import get_settings
from app.styled_buttons import inline_keyboard, styled_button


def public_channel_cta_keyboard() -> dict | None:
    """Зелёная CTA-кнопка под постами в открытом канале.

    Кнопка ведёт в бота, где пользователь может выбрать тариф и оплатить доступ
    в приватный канал через Stars или PayKassa.
    """
    settings = get_settings()

    if not settings.public_channel_cta_enabled:
        return None

    username = settings.telegram_bot_username.strip().lstrip("@")
    if not username:
        return None

    return inline_keyboard([
        [
            styled_button(
                "🔒 Получить VIP доступ / Get VIP access",
                url=f"https://t.me/{username}?start=vip",
                style="success",
            )
        ]
    ])
