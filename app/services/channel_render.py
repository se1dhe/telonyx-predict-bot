from __future__ import annotations

def private_summary(summary: str) -> str:
    return (
        "🔒 <b>VIP / PRIVATE PICKS</b>\n"
        "All selected matches for today.\n\n"
        + summary
        + "\n\n🇬🇧 <b>Note:</b> These are analytical predictions, not guaranteed profit."
    )

def public_summary_from_private(summary: str, detail: str | None) -> str:
    if detail:
        return (
            "🟢 <b>Free pick of the day / Бесплатный прогноз дня</b>\n\n"
            + detail
            + "\n\n🔒 More picks are available in the private channel.\n"
            "Больше прогнозов доступно в приватном канале."
        )

    return (
        "🟢 <b>Free pick of the day / Бесплатный прогноз дня</b>\n\n"
        + summary
        + "\n\n🔒 More picks are available in the private channel."
    )
