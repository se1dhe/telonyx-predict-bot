from __future__ import annotations

from app.config import get_settings
from app.i18n import t, normalize_lang
from app.services.subscriptions import get_price_stars, get_price_usdt
from app.styled_buttons import inline_keyboard, styled_button


LANG_LABELS = {
    "uk": "🇺🇦 Українська",
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
}


def lang_keyboard() -> dict:
    """Клавиатура выбора языка."""
    settings = get_settings()
    rows = []
    for lang in settings.supported_languages:
        rows.append([styled_button(LANG_LABELS.get(lang, lang), callback_data=f"lang:{lang}", style="primary")])
    return inline_keyboard(rows)


def main_menu(lang: str = "uk") -> dict:
    settings = get_settings()
    lang = normalize_lang(lang)
    rows = [
        [styled_button(t(lang, "buy"), callback_data="menu:plans", style="primary")],
        [styled_button(t(lang, "cabinet"), callback_data="menu:cabinet")],
    ]

    public_channel = settings.public_channel_for(lang)
    if public_channel:
        rows.append([
            styled_button(
                t(lang, "public_channel"),
                url=f"https://t.me/{public_channel.lstrip('@')}",
                style="success",
            )
        ])

    rows.append([styled_button(t(lang, "language"), callback_data="menu:language")])
    return inline_keyboard(rows)


def plans_keyboard(lang: str = "uk") -> dict:
    settings = get_settings()
    lang = normalize_lang(lang)
    rows = []
    for code, label_key in [("1d", "plan_1d"), ("3d", "plan_3d"), ("30d", "plan_30d")]:
        rows.append([
            styled_button(
                f"{t(lang, label_key)} · {get_price_usdt(settings, code):.2f} USDT / {get_price_stars(settings, code)} ⭐",
                callback_data=f"plan:{code}",
                style="primary",
            )
        ])
    rows.append([styled_button(t(lang, "back"), callback_data="menu:main")])
    return inline_keyboard(rows)


def payment_keyboard(lang: str, plan_code: str) -> dict:
    lang = normalize_lang(lang)
    return inline_keyboard([
        [styled_button(t(lang, "pay_stars"), callback_data=f"pay:stars:{plan_code}", style="success")],
        [styled_button(t(lang, "pay_usdt"), callback_data=f"pay:paykassa:{plan_code}", style="success")],
        [styled_button(t(lang, "back"), callback_data="menu:plans")],
    ])


def pay_url_keyboard(lang: str, url: str) -> dict:
    lang = normalize_lang(lang)
    return inline_keyboard([
        [styled_button(t(lang, "pay_usdt"), url=url, style="success")],
        [styled_button(t(lang, "back"), callback_data="menu:main")],
    ])


def back_keyboard(lang: str = "uk") -> dict:
    lang = normalize_lang(lang)
    return inline_keyboard([[styled_button(t(lang, "back"), callback_data="menu:main")]])


def renew_subscription_keyboard(lang: str = "uk") -> dict:
    """Клавиатура продления подписки прямо из уведомлений."""
    settings = get_settings()
    lang = normalize_lang(lang)
    rows = []

    plan_rows = [
        ("1d", "renew_1d"),
        ("3d", "renew_3d"),
        ("30d", "renew_30d"),
    ]

    for code, label_key in plan_rows:
        label = t(lang, label_key)
        usdt = get_price_usdt(settings, code)
        stars = get_price_stars(settings, code)

        rows.append([
            styled_button(
                f"⭐ {label} · {stars} ⭐",
                callback_data=f"pay:stars:{code}",
                style="success",
            ),
            styled_button(
                f"💵 {label} · {usdt:.2f} USDT",
                callback_data=f"pay:paykassa:{code}",
                style="primary",
            ),
        ])

    rows.append([styled_button(t(lang, "buy"), callback_data="menu:plans", style="primary")])
    return inline_keyboard(rows)
