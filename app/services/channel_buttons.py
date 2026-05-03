from __future__ import annotations

from app.config import get_settings
from app.i18n import normalize_lang
from app.styled_buttons import inline_keyboard, styled_button


CTA_TEXT = {
    "uk": "🔒 Отримати VIP-доступ",
    "en": "🔒 Get VIP access",
    "ru": "🔒 Получить VIP-доступ",
}


def public_channel_cta_keyboard(lang: str = "uk") -> dict | None:
    """Зелёная CTA-кнопка под постами в открытом канале.

    Кнопка ведёт в бота с языковым start-параметром.
    """
    settings = get_settings()
    lang = normalize_lang(lang)

    if not settings.public_channel_cta_enabled:
        return None

    username = settings.telegram_bot_username.strip().lstrip("@")
    if not username:
        return None

    return inline_keyboard([
        [
            styled_button(
                CTA_TEXT.get(lang, CTA_TEXT["uk"]),
                url=f"https://t.me/{username}?start=vip_{lang}",
                style="success",
            )
        ]
    ])
