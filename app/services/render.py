from __future__ import annotations

from datetime import datetime

from app.schemas import AiPick, CandidateContext


def html_escape(value: object) -> str:
    """Экранирование HTML для Telegram."""
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def normalize_bet_label(label: str) -> str:
    """Сделать название ставки понятнее для обычного пользователя."""
    value = label.strip()

    replacements = {
        "ТБ 1.5": "Тотал больше 1.5 гола",
        "ТБ 2.5": "Тотал больше 2.5 гола",
        "ОЗ Да": "Обе команды забьют — Да",
        "Обе забьют — Да": "Обе команды забьют — Да",
        "DNB": "фора 0 / Draw No Bet",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    return value


def bet_instruction(p: AiPick) -> str:
    """Объяснение, что именно искать у букмекера."""
    code = p.main_bet_code

    if code == "OVER_1_5":
        return "В линии букмекера ищи рынок: <b>Тотал голов → Больше 1.5</b>."
    if code == "OVER_2_5":
        return "В линии букмекера ищи рынок: <b>Тотал голов → Больше 2.5</b>."
    if code == "BTTS_YES":
        return "В линии букмекера ищи рынок: <b>Обе команды забьют → Да</b>."
    if code == "HOME_DOUBLE_CHANCE":
        return "В линии букмекера ищи рынок: <b>Двойной шанс → 1X</b>."
    if code == "AWAY_DOUBLE_CHANCE":
        return "В линии букмекера ищи рынок: <b>Двойной шанс → X2</b>."
    if code == "HOME_OR_DRAW_OVER_1_5":
        return "Ищи комбинированный рынок: <b>1X + ТБ 1.5</b>. Если такого нет — безопаснее взять просто <b>ТБ 1.5</b>."
    if code == "AWAY_OR_DRAW_OVER_1_5":
        return "Ищи комбинированный рынок: <b>X2 + ТБ 1.5</b>. Если такого нет — безопаснее взять просто <b>ТБ 1.5</b>."
    if code == "HOME_DNB":
        return "Ищи рынок: <b>Победа хозяев с форой 0</b> или <b>Draw No Bet</b>. При ничьей обычно возврат."
    if code == "AWAY_DNB":
        return "Ищи рынок: <b>Победа гостей с форой 0</b> или <b>Draw No Bet</b>. При ничьей обычно возврат."

    return "Если такого рынка нет в линии — лучше пропустить матч, а не заменять ставку наугад."


def confidence_text(confidence: int) -> str:
    """Человеческое описание уверенности."""
    if confidence >= 75:
        return "высокая"
    if confidence >= 60:
        return "средняя"
    if confidence >= 45:
        return "умеренная"
    return "низкая"


def risk_emoji(risk: str) -> str:
    """Эмодзи риска."""
    risk_lower = risk.lower()
    if "низ" in risk_lower:
        return "🟢"
    if "выс" in risk_lower:
        return "🔴"
    return "🟡"


def format_match_time(ctx: CandidateContext | None) -> str:
    """Отформатировать дату и время матча."""
    if not ctx:
        return "время уточнить в линии букмекера"

    raw = ctx.start_time or ""

    # API_FOOTBALL обычно отдаёт ISO.
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass

    # LOCAL часто отдаёт YYYY-MM-DD HH:MM или YYYY-MM-DD.
    if len(raw) >= 16 and raw[4] == "-" and raw[7] == "-":
        try:
            dt = datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            pass

    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
            return dt.strftime("%d.%m.%Y, время уточнить")
        except Exception:
            pass

    return html_escape(raw) if raw else "время уточнить в линии букмекера"


def compact_reason(text: str, max_len: int = 420) -> str:
    """Сократить длинное объяснение для сводки."""
    text = " ".join(str(text).split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def render_daily_summary(
    picks: list[AiPick],
    rejected_summary: list[str],
    provider: str = "",
    contexts_by_id: dict[str, CandidateContext] | None = None,
) -> str:
    """Понятная сводка для пользователя."""
    contexts_by_id = contexts_by_id or {}

    if not picks:
        return (
            "⚠️ <b>На сегодня нет достаточно чистых матчей.</b>\n\n"
            "Бот не будет давать ставку ради ставки. Лучше пропустить день, чем брать сомнительный матч."
        )

    lines = [
        "🏆 <b>Футбольные прогнозы на сегодня</b>",
        "",
        "Я отобрал только матчи, где статистика выглядит достаточно чисто.",
        "Ниже — что именно искать в линии букмекера.",
        "",
        f"🧩 <b>Источник данных:</b> {html_escape(provider or 'LOCAL/API')}",
        f"📌 <b>Матчей в отборе:</b> {len(picks)}",
        "",
    ]

    for i, p in enumerate(picks, start=1):
        ctx = contexts_by_id.get(p.fixture_id)
        match_time = format_match_time(ctx)
        league = f"{ctx.country} • {ctx.league_name}" if ctx else "лига уточняется"
        bet = normalize_bet_label(p.main_bet_label)

        lines.extend(
            [
                f"<b>{i}. {html_escape(p.match_title)}</b>",
                f"🗓 <b>Дата/время:</b> {html_escape(match_time)}",
                f"🏟 <b>Турнир:</b> {html_escape(league)}",
                f"✅ <b>Что ставить:</b> {html_escape(bet)}",
                f"🎯 <b>Ожидаемый счёт:</b> {html_escape(p.expected_score)}",
                f"{risk_emoji(p.risk_level)} <b>Риск:</b> {html_escape(p.risk_level)}",
                f"🧠 <b>Уверенность:</b> {p.confidence}/100 ({confidence_text(p.confidence)})",
                f"🔗 <a href=\"{html_escape(p.tracking_url)}\">Открыть матч</a>",
                "",
            ]
        )

    lines.extend(
        [
            "💰 <b>Как использовать:</b>",
            "• не ставь весь банк на один матч;",
            "• не заменяй рынок на другой, если нужной ставки нет;",
            "• если коэффициент сильно просел — лучше пропустить;",
            "• это аналитика, а не гарантия выигрыша.",
        ]
    )

    return "\n".join(lines)


def render_pick_detail(p: AiPick, ctx: CandidateContext | None = None) -> str:
    """Детальный прогноз по одному матчу."""
    match_time = format_match_time(ctx)
    league = f"{ctx.country} • {ctx.league_name}" if ctx else "лига уточняется"
    bet = normalize_bet_label(p.main_bet_label)
    safe_bet = normalize_bet_label(p.safe_bet_label)
    risky_bet = normalize_bet_label(p.risky_bet_label)

    warnings = ""
    if p.data_warnings:
        warnings = "\n\n⚠️ <b>Что важно учитывать:</b>\n" + "\n".join(
            f"• {html_escape(x)}" for x in p.data_warnings[:4]
        )

    data_block = ""
    if ctx:
        h = ctx.home_metrics
        a = ctx.away_metrics
        data_block = (
            "\n\n📈 <b>Коротко по цифрам:</b>\n"
            f"• {html_escape(ctx.home_team)}: {h.wins}-{h.draws}-{h.losses}, голы {h.goals_for}:{h.goals_against}, ТБ1.5 {h.over15}/{max(1, h.matches)}, ОЗ {h.btts}/{max(1, h.matches)}\n"
            f"• {html_escape(ctx.away_team)}: {a.wins}-{a.draws}-{a.losses}, голы {a.goals_for}:{a.goals_against}, ТБ1.5 {a.over15}/{max(1, a.matches)}, ОЗ {a.btts}/{max(1, a.matches)}"
        )

    return (
        f"⚽️ <b>{html_escape(p.match_title)}</b>\n"
        f"🗓 <b>Дата/время:</b> {html_escape(match_time)}\n"
        f"🏟 <b>Турнир:</b> {html_escape(league)}\n\n"
        f"✅ <b>Основная ставка:</b> {html_escape(bet)}\n"
        f"📌 {bet_instruction(p)}\n\n"
        f"🛡 <b>Более осторожно:</b> {html_escape(safe_bet)}\n"
        f"🔥 <b>Рискованнее:</b> {html_escape(risky_bet)}\n"
        f"{risk_emoji(p.risk_level)} <b>Риск:</b> {html_escape(p.risk_level)}\n"
        f"🧠 <b>Уверенность:</b> {p.confidence}/100 ({confidence_text(p.confidence)})\n"
        f"🎯 <b>Ожидаемый счёт:</b> {html_escape(p.expected_score)}\n\n"
        f"📊 <b>Кто ближе к победе:</b> {html_escape(p.predicted_winner)}\n"
        f"🥅 <b>Кто вероятнее забьёт:</b> {html_escape(p.who_should_score)}\n\n"
        f"💎 <b>Почему матч в отборе:</b>\n{html_escape(compact_reason(p.why_this_match_is_gold, 520))}\n\n"
        f"🧠 <b>Разбор:</b>\n{html_escape(compact_reason(p.reasoning, 900))}"
        f"{data_block}"
        f"{warnings}\n\n"
        f"🔗 <a href=\"{html_escape(p.tracking_url)}\">Открыть матч / проверить результат</a>\n\n"
        f"⚠️ <i>Не финансовый совет. Ставь только сумму, которую готов потерять.</i>"
    )


def render_result_line(match_title: str, score: str, bet: str, success: bool) -> str:
    """Строка результата прогноза."""
    mark = "✅ зашло" if success else "❌ не зашло"
    return (
        f"{mark} — <b>{html_escape(match_title)}</b>\n"
        f"Счёт: <b>{html_escape(score)}</b>\n"
        f"Прогноз: <b>{html_escape(normalize_bet_label(bet))}</b>"
    )
