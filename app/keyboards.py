from __future__ import annotations

from app.config import get_settings
from app.i18n import t
from app.services.subscriptions import get_price_stars, get_price_usdt
from app.styled_buttons import inline_keyboard, styled_button


def lang_keyboard() -> dict:
    return inline_keyboard([
        [
            styled_button("🇷🇺 Русский", callback_data="lang:ru", style="primary"),
            styled_button("🇬🇧 English", callback_data="lang:en", style="primary"),
        ]
    ])


def main_menu(lang: str) -> dict:
    settings = get_settings()
    return inline_keyboard([
        [styled_button(t(lang, "buy"), callback_data="menu:plans", style="primary")],
        [styled_button(t(lang, "cabinet"), callback_data="menu:cabinet")],
        [
            styled_button(t(lang, "public_channel"), url=f"https://t.me/{settings.telegram_public_channel.lstrip('@')}", style="success"),
            styled_button(t(lang, "language"), callback_data="menu:language"),
        ],
    ])


def plans_keyboard(lang: str) -> dict:
    settings = get_settings()
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
    return inline_keyboard([
        [styled_button(t(lang, "pay_stars"), callback_data=f"pay:stars:{plan_code}", style="success")],
        [styled_button(t(lang, "pay_usdt"), callback_data=f"pay:paykassa:{plan_code}", style="success")],
        [styled_button(t(lang, "back"), callback_data="menu:plans")],
    ])


def pay_url_keyboard(lang: str, url: str) -> dict:
    return inline_keyboard([
        [styled_button("💵 Pay USDT", url=url, style="success")],
        [styled_button(t(lang, "back"), callback_data="menu:main")],
    ])


def back_keyboard(lang: str) -> dict:
    return inline_keyboard([
        [styled_button(t(lang, "back"), callback_data="menu:main")]
    ])
