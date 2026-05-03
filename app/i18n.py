from __future__ import annotations

TEXTS = {
    "uk": {
        "choose_language": "Оберіть мову:",
        "language_saved": "✅ Мову збережено.",
        "start": (
            "⚽️ <b>TelOnyx Predict</b>\n\n"
            "Бот щодня знаходить перспективні футбольні матчі, відсіює слабкі варіанти "
            "та публікує прогнози.\n\n"
            "🟢 Відкритий канал: 1 найсильніший матч на добу.\n"
            "🔒 Приватний канал: усі якісні матчі, детальний розбір, статистика та winrate.\n\n"
            "⚠️ Прогнози не гарантують прибуток. Використовуйте банкрол-менеджмент."
        ),
        "menu": "👇 Оберіть дію:",
        "buy": "🔒 Доступ до приватного каналу",
        "cabinet": "👤 Особистий кабінет",
        "public_channel": "📢 Відкритий канал",
        "language": "🌐 Змінити мову",
        "back": "⬅️ Назад",
        "plans": "Оберіть термін доступу:",
        "pay_stars": "⭐ Оплатити Stars",
        "pay_usdt": "💵 Оплатити USDT",
        "plan_1d": "1 день",
        "plan_3d": "3 дні",
        "plan_30d": "1 місяць",
        "payment_created": "Рахунок створено. Після оплати доступ буде видано автоматично.",
        "channel_unavailable": "⚠️ Приватний канал для цієї мови поки не підключено. Оберіть іншу мову або спробуйте пізніше.",
        "public_unavailable": "⚠️ Публічний канал для цієї мови поки не підключено.",
        "paykassa_disabled": "PayKassa поки не налаштована. Доступна оплата Stars.",
        "paykassa_error": "⚠️ PayKassa тимчасово не змогла створити рахунок. Спробуйте оплату Stars або повторіть пізніше.",
        "cabinet_title": "👤 <b>Особистий кабінет</b>",
        "active_until": "🔒 Доступ активний до: <b>{date}</b>",
        "no_access": "🔓 Активного доступу поки немає",
        "transactions": "Історія транзакцій",
        "no_transactions": "Транзакцій поки немає",
        "paid": "✅ Оплату отримано. Доступ видано.",
        "invite": "🔗 Ваше посилання до приватного каналу:\n{url}\n\nПосилання одноразове та діє обмежений час.",
        "invoice_title": "Доступ TelOnyx Predict",
        "invoice_desc": "Доступ до приватного каналу TelOnyx Predict на {days} дн.",
        "subscription_expire_24h": "⏰ Ваш доступ закінчиться приблизно через 24 години. Продовжте підписку, щоб не втратити прогнози.",
        "subscription_expire_5h": "⏰ Ваш доступ закінчиться приблизно через 5 годин.",
        "subscription_expire_1h": "⚠️ Ваш доступ закінчиться приблизно через 1 годину. Після завершення бот видалить вас із приватного каналу.",
        "subscription_expired": "🔒 Ваша підписка закінчилась. Доступ до приватного каналу зупинено. Ви можете продовжити доступ нижче.",
        "renew_subscription": "🔄 Продовжити підписку",
        "renew_1d": "Продовжити на 1 день",
        "renew_3d": "Продовжити на 3 дні",
        "renew_30d": "Продовжити на місяць",
    },
    "en": {
        "choose_language": "Choose language:",
        "language_saved": "✅ Language saved.",
        "start": (
            "⚽️ <b>TelOnyx Predict</b>\n\n"
            "The bot finds promising football matches every day, filters out weak options "
            "and publishes predictions.\n\n"
            "🟢 Public channel: 1 strongest pick per day.\n"
            "🔒 Private channel: all quality picks, detailed analysis, stats and winrate.\n\n"
            "⚠️ Predictions are not guaranteed profit. Use bankroll management."
        ),
        "menu": "👇 Choose an action:",
        "buy": "🔒 Private channel access",
        "cabinet": "👤 My account",
        "public_channel": "📢 Public channel",
        "language": "🌐 Change language",
        "back": "⬅️ Back",
        "plans": "Choose access period:",
        "pay_stars": "⭐ Pay with Stars",
        "pay_usdt": "💵 Pay with USDT",
        "plan_1d": "1 day",
        "plan_3d": "3 days",
        "plan_30d": "1 month",
        "payment_created": "Invoice created. Access will be granted automatically after payment.",
        "channel_unavailable": "⚠️ The private channel for this language is not connected yet. Choose another language or try later.",
        "public_unavailable": "⚠️ The public channel for this language is not connected yet.",
        "paykassa_disabled": "PayKassa is not configured yet. Stars payment is available.",
        "paykassa_error": "⚠️ PayKassa could not create an invoice right now. Try Stars or repeat later.",
        "cabinet_title": "👤 <b>My account</b>",
        "active_until": "🔒 Access active until: <b>{date}</b>",
        "no_access": "🔓 No active access yet",
        "transactions": "Transaction history",
        "no_transactions": "No transactions yet",
        "paid": "✅ Payment received. Access granted.",
        "invite": "🔗 Your private channel invite link:\n{url}\n\nThe link is one-time and valid for a limited time.",
        "invoice_title": "TelOnyx Predict access",
        "invoice_desc": "TelOnyx Predict private channel access for {days} days.",
        "subscription_expire_24h": "⏰ Your access expires in about 24 hours. Renew it to keep receiving predictions.",
        "subscription_expire_5h": "⏰ Your access expires in about 5 hours.",
        "subscription_expire_1h": "⚠️ Your access expires in about 1 hour. After expiration the bot will remove you from the private channel.",
        "subscription_expired": "🔒 Your subscription has expired. Private channel access has been stopped. You can renew below.",
        "renew_subscription": "🔄 Renew subscription",
        "renew_1d": "Renew for 1 day",
        "renew_3d": "Renew for 3 days",
        "renew_30d": "Renew for 1 month",
    },
    "ru": {
        "choose_language": "Выберите язык:",
        "language_saved": "✅ Язык сохранён.",
        "start": (
            "⚽️ <b>TelOnyx Predict</b>\n\n"
            "Бот каждый день находит перспективные футбольные матчи, отсеивает слабые варианты "
            "и публикует прогнозы.\n\n"
            "🟢 Открытый канал: 1 самый сильный матч в день.\n"
            "🔒 Приватный канал: все качественные матчи, детальный разбор, статистика и winrate.\n\n"
            "⚠️ Прогнозы не гарантируют прибыль. Используйте банкролл-менеджмент."
        ),
        "menu": "👇 Выберите действие:",
        "buy": "🔒 Доступ в приватный канал",
        "cabinet": "👤 Личный кабинет",
        "public_channel": "📢 Открытый канал",
        "language": "🌐 Сменить язык",
        "back": "⬅️ Назад",
        "plans": "Выберите срок доступа:",
        "pay_stars": "⭐ Оплатить Stars",
        "pay_usdt": "💵 Оплатить USDT",
        "plan_1d": "1 день",
        "plan_3d": "3 дня",
        "plan_30d": "1 месяц",
        "payment_created": "Счёт создан. После оплаты доступ будет выдан автоматически.",
        "channel_unavailable": "⚠️ Приватный канал для этого языка пока не подключён. Выберите другой язык или попробуйте позже.",
        "public_unavailable": "⚠️ Публичный канал для этого языка пока не подключён.",
        "paykassa_disabled": "PayKassa пока не настроена. Доступна оплата Stars.",
        "paykassa_error": "⚠️ PayKassa временно не смогла создать счёт. Попробуйте Stars или повторите позже.",
        "cabinet_title": "👤 <b>Личный кабинет</b>",
        "active_until": "🔒 Доступ активен до: <b>{date}</b>",
        "no_access": "🔓 Активного доступа пока нет",
        "transactions": "История транзакций",
        "no_transactions": "Транзакций пока нет",
        "paid": "✅ Оплата получена. Доступ выдан.",
        "invite": "🔗 Ваша ссылка в приватный канал:\n{url}\n\nСсылка одноразовая и действует ограниченное время.",
        "invoice_title": "Доступ TelOnyx Predict",
        "invoice_desc": "Доступ в приватный канал TelOnyx Predict на {days} дн.",
        "subscription_expire_24h": "⏰ Ваш доступ закончится примерно через 24 часа. Продлите подписку, чтобы не потерять прогнозы.",
        "subscription_expire_5h": "⏰ Ваш доступ закончится примерно через 5 часов.",
        "subscription_expire_1h": "⚠️ Ваш доступ закончится примерно через 1 час. После завершения бот удалит вас из приватного канала.",
        "subscription_expired": "🔒 Ваша подписка закончилась. Доступ в приватный канал остановлен. Вы можете продлить доступ ниже.",
        "renew_subscription": "🔄 Продлить подписку",
        "renew_1d": "Продлить на 1 день",
        "renew_3d": "Продлить на 3 дня",
        "renew_30d": "Продлить на месяц",
    },
}


def normalize_lang(lang: str | None) -> str:
    value = (lang or "").strip().lower()
    return value if value in TEXTS else "uk"


def t(lang: str, key: str, **kwargs) -> str:
    value = TEXTS[normalize_lang(lang)].get(key, TEXTS["uk"].get(key, key))
    return value.format(**kwargs)
