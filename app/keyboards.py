from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from app.config import get_settings
from app.i18n import t
from app.services.subscriptions import get_price_stars, get_price_usdt

def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
        ]
    ])

def main_menu(lang: str) -> InlineKeyboardMarkup:
    settings = get_settings()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "buy"), callback_data="menu:plans")],
        [InlineKeyboardButton(text=t(lang, "cabinet"), callback_data="menu:cabinet")],
        [
            InlineKeyboardButton(text=t(lang, "public_channel"), url=f"https://t.me/{settings.telegram_public_channel.lstrip('@')}"),
            InlineKeyboardButton(text=t(lang, "language"), callback_data="menu:language"),
        ],
    ])

def plans_keyboard(lang: str) -> InlineKeyboardMarkup:
    settings = get_settings()
    rows = []
    for code, label_key in [("1d", "plan_1d"), ("3d", "plan_3d"), ("30d", "plan_30d")]:
        rows.append([
            InlineKeyboardButton(
                text=f"{t(lang, label_key)} · {get_price_usdt(settings, code):.2f} USDT / {get_price_stars(settings, code)} ⭐",
                callback_data=f"plan:{code}",
            )
        ])
    rows.append([InlineKeyboardButton(text=t(lang, "back"), callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def payment_keyboard(lang: str, plan_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "pay_stars"), callback_data=f"pay:stars:{plan_code}")],
        [InlineKeyboardButton(text=t(lang, "pay_usdt"), callback_data=f"pay:paykassa:{plan_code}")],
        [InlineKeyboardButton(text=t(lang, "back"), callback_data="menu:plans")],
    ])

def pay_url_keyboard(lang: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Pay USDT", url=url)],
        [InlineKeyboardButton(text=t(lang, "back"), callback_data="menu:main")],
    ])

def back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "back"), callback_data="menu:main")]
    ])
