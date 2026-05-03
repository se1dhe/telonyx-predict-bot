from __future__ import annotations

TEXTS = {
    "ru": {
        "choose_lang": "🌍 Выберите язык / Choose language",
        "lang_saved": "✅ Язык сохранён: Русский",
        "start": (
            "⚽️ <b>TelOnyx Predict</b>\n\n"
            "Это бот футбольной аналитики: каждый день он ищет перспективные матчи, "
            "фильтрует слабые варианты и публикует прогнозы.\n\n"
            "🟢 Бесплатный канал: 1 лучший матч в сутки.\n"
            "🔒 Приватный канал: все найденные качественные матчи, подробный разбор, статистика и winrate.\n\n"
            "⚠️ Прогнозы не являются гарантией выигрыша. Используйте банкролл-менеджмент."
        ),
        "menu": "👇 Выберите действие:",
        "buy": "🔒 Доступ в приватный канал",
        "cabinet": "👤 Личный кабинет",
        "language": "🌍 Язык",
        "public_channel": "📢 Бесплатный канал",
        "back": "⬅️ Назад",
        "plans": "Выберите срок доступа:",
        "pay_stars": "⭐ Оплатить Stars",
        "pay_usdt": "💵 Оплатить USDT",
        "plan_1d": "1 день",
        "plan_3d": "3 дня",
        "plan_30d": "1 месяц",
        "payment_created": "Счёт создан. После оплаты доступ будет выдан автоматически.",
        "paykassa_disabled": "PayKassa пока не настроена. Доступна оплата Stars.",
        "cabinet_title": "👤 <b>Личный кабинет</b>",
        "active_until": "🔒 Доступ активен до: <b>{date}</b>",
        "no_access": "🔓 Активного доступа пока нет",
        "transactions": "История транзакций",
        "no_transactions": "Транзакций пока нет",
        "paid": "✅ Оплата получена. Доступ выдан.",
        "invite": "🔗 Ваша ссылка в приватный канал:\n{url}\n\nСсылка одноразовая и действует ограниченное время.",
        "invoice_title": "Доступ TelOnyx Predict",
        "invoice_desc": "Доступ в приватный канал TelOnyx Predict на {days} дн.",
        "subscription_expire_24h": "⏰ Ваш доступ в приватный канал закончится примерно через 24 часа. Продлите подписку, чтобы не потерять прогнозы.",
        "subscription_expire_5h": "⏰ Ваш доступ в приватный канал закончится примерно через 5 часов.",
        "subscription_expire_1h": "⚠️ Ваш доступ закончится примерно через 1 час. После окончания бот удалит вас из приватного канала.",
        "subscription_expired": "🔒 Ваша подписка закончилась. Доступ в приватный канал остановлен. Вы можете продлить доступ в меню бота.",
    },
    "en": {
        "choose_lang": "🌍 Choose language / Выберите язык",
        "lang_saved": "✅ Language saved: English",
        "start": (
            "⚽️ <b>TelOnyx Predict</b>\n\n"
            "A football analytics bot: every day it searches for promising matches, "
            "filters weak picks and publishes predictions.\n\n"
            "🟢 Free channel: 1 strongest daily pick.\n"
            "🔒 Private channel: all qualified picks, detailed analysis, stats and winrate.\n\n"
            "⚠️ Predictions are not guaranteed profit. Use bankroll management."
        ),
        "menu": "👇 Choose an action:",
        "buy": "🔒 Private channel access",
        "cabinet": "👤 Account",
        "language": "🌍 Language",
        "public_channel": "📢 Free channel",
        "back": "⬅️ Back",
        "plans": "Choose access period:",
        "pay_stars": "⭐ Pay with Stars",
        "pay_usdt": "💵 Pay with USDT",
        "plan_1d": "1 day",
        "plan_3d": "3 days",
        "plan_30d": "1 month",
        "payment_created": "Invoice created. Access will be granted automatically after payment.",
        "paykassa_disabled": "PayKassa is not configured yet. Stars payment is available.",
        "cabinet_title": "👤 <b>Account</b>",
        "active_until": "🔒 Access active until: <b>{date}</b>",
        "no_access": "🔓 No active access yet",
        "transactions": "Transaction history",
        "no_transactions": "No transactions yet",
        "paid": "✅ Payment received. Access granted.",
        "invite": "🔗 Your private channel invite link:\n{url}\n\nThe link is single-use and time-limited.",
        "invoice_title": "TelOnyx Predict Access",
        "invoice_desc": "Access to TelOnyx Predict private channel for {days} days.",
        "subscription_expire_24h": "⏰ Your private channel access expires in about 24 hours. Renew to keep receiving picks.",
        "subscription_expire_5h": "⏰ Your private channel access expires in about 5 hours.",
        "subscription_expire_1h": "⚠️ Your access expires in about 1 hour. After expiration the bot will remove you from the private channel.",
        "subscription_expired": "🔒 Your subscription has expired. Private channel access has been stopped. You can renew from the bot menu.",
    },
}

def t(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in TEXTS else "ru"
    value = TEXTS[lang].get(key, TEXTS["ru"].get(key, key))
    return value.format(**kwargs)
