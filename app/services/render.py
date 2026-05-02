from __future__ import annotations
from app.schemas import AiPick

def html_escape(value: object) -> str:
    """Экранировать HTML."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def render_daily_summary(picks: list[AiPick], rejected_summary: list[str], provider: str = "") -> str:
    """Сводка на день."""
    if not picks:
        return "⚠️ На сегодня бот не нашёл достаточно сильных матчей. Лучше пропустить день."
    lines = ["🏆 <b>Золотой отбор матчей на сегодня</b>", f"Источник данных: <b>{html_escape(provider)}</b>", "", "Бот отсеял сомнительные матчи и оставил лучшие кандидаты.", ""]
    for i, p in enumerate(picks, 1):
        lines.append(f"{i}. <b>{html_escape(p.match_title)}</b>\n   ✅ Ставка: <b>{html_escape(p.main_bet_label)}</b>\n   🧠 Уверенность: {p.confidence}/100\n   🎯 Счёт: {html_escape(p.expected_score)}\n   🔗 <a href=\"{html_escape(p.tracking_url)}\">SofaScore</a>")
    if rejected_summary:
        lines.append("\n🗑 <b>Почему часть матчей отсеяна:</b>")
        for row in rejected_summary[:5]:
            lines.append(f"• {html_escape(row)}")
    lines.append("\n⚠️ Это аналитика, не гарантия выигрыша.")
    return "\n".join(lines)

def render_pick_detail(p: AiPick) -> str:
    """Детальный прогноз."""
    warnings = ""
    if p.data_warnings:
        warnings = "\n\n⚠️ <b>Ограничения:</b>\n" + "\n".join(f"• {html_escape(x)}" for x in p.data_warnings[:4])
    return (f"⚽️ <b>{html_escape(p.match_title)}</b>\n\n"
            f"📊 <b>Кто ближе к победе:</b> {html_escape(p.predicted_winner)}\n"
            f"🥅 <b>Кто должен забить:</b> {html_escape(p.who_should_score)}\n"
            f"✅ <b>Основная ставка:</b> {html_escape(p.main_bet_label)}\n"
            f"🛡 <b>Осторожнее:</b> {html_escape(p.safe_bet_label)}\n"
            f"🔥 <b>Рискованно:</b> {html_escape(p.risky_bet_label)}\n"
            f"📈 <b>Уверенность:</b> {p.confidence}/100\n"
            f"🎯 <b>Ожидаемый счёт:</b> {html_escape(p.expected_score)}\n\n"
            f"💎 <b>Почему матч прошёл фильтр:</b>\n{html_escape(p.why_this_match_is_gold)}\n\n"
            f"🧠 <b>Разбор:</b>\n{html_escape(p.reasoning)}{warnings}\n\n"
            f"🔗 <a href=\"{html_escape(p.tracking_url)}\">Открыть матч на SofaScore</a>")

def render_result_line(match_title: str, score: str, bet: str, success: bool) -> str:
    """Строка результата."""
    mark = "✅ зашло" if success else "❌ не зашло"
    return f"{mark} — <b>{html_escape(match_title)}</b>\nСчёт: <b>{html_escape(score)}</b>\nПрогноз: <b>{html_escape(bet)}</b>"
