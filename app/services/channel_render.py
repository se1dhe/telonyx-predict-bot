from __future__ import annotations

from app.i18n import normalize_lang


PRIVATE_HEAD = {
    "uk": "🔒 <b>VIP-прогнози</b>\nУсі відібрані матчі на сьогодні.\n\n",
    "en": "🔒 <b>VIP predictions</b>\nAll selected matches for today.\n\n",
    "ru": "🔒 <b>VIP-прогнозы</b>\nВсе отобранные матчи на сегодня.\n\n",
}

PRIVATE_FOOT = {
    "uk": "\n\n⚠️ <b>Важливо:</b> це аналітичні прогнози, а не гарантія прибутку.",
    "en": "\n\n⚠️ <b>Important:</b> these are analytical predictions, not guaranteed profit.",
    "ru": "\n\n⚠️ <b>Важно:</b> это аналитические прогнозы, а не гарантия прибыли.",
}

PUBLIC_HEAD = {
    "uk": "🟢 <b>Безкоштовний прогноз дня</b>\n\n",
    "en": "🟢 <b>Free pick of the day</b>\n\n",
    "ru": "🟢 <b>Бесплатный прогноз дня</b>\n\n",
}

PUBLIC_FOOT = {
    "uk": "\n\n🔒 Більше прогнозів доступно в приватному каналі.",
    "en": "\n\n🔒 More predictions are available in the private channel.",
    "ru": "\n\n🔒 Больше прогнозов доступно в приватном канале.",
}


def private_summary(summary: str, lang: str = "uk") -> str:
    lang = normalize_lang(lang)
    return PRIVATE_HEAD[lang] + summary + PRIVATE_FOOT[lang]


def public_summary_from_private(summary: str, detail: str | None, lang: str = "uk") -> str:
    lang = normalize_lang(lang)
    content = detail if detail else summary
    return PUBLIC_HEAD[lang] + content + PUBLIC_FOOT[lang]
