from __future__ import annotations

from app.config import get_settings


def styled_button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
) -> dict:
    """Inline-кнопка с поддержкой новых полей Bot API.

    Пока aiogram может не иметь этих полей в типах InlineKeyboardButton,
    поэтому клавиатуры возвращаются как raw dict. Telegram проигнорирует поля,
    если клиент/сервер их не поддерживает.
    """
    button: dict = {"text": text}

    if callback_data:
        button["callback_data"] = callback_data
    if url:
        button["url"] = url

    settings = get_settings()
    if settings.styled_buttons_enabled:
        if style:
            button["style"] = style
        if icon_custom_emoji_id:
            button["icon_custom_emoji_id"] = icon_custom_emoji_id

    return button


def inline_keyboard(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}
