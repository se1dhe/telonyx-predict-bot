from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus
import re

from app.config import get_settings
from app.i18n import normalize_lang
from app.schemas import AiPick, CandidateContext


def html_escape(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


LABELS = {
    "uk": {
        "daily_title": "🏆 <b>Футбольні прогнози на сьогодні</b>",
        "daily_intro": "Бот відібрав тільки матчі, де статистика виглядає достатньо чисто.",
        "what_to_find": "Нижче — що саме шукати в лінії букмекера.",
        "count": "📌 <b>Матчів у відборі:</b>",
        "bet": "✅ <b>Ставка:</b>",
        "confidence": "🧠 <b>Упевненість:</b>",
        "score": "🎯 <b>Очікуваний рахунок:</b>",
        "risk": "<b>Ризик:</b>",
        "open_match": "Відкрити матч / перевірити результат",
        "bookmaker": "Відкрити лінію",
        "how": "💰 <b>Як використовувати:</b>\n• не став увесь банк на один матч;\n• не замінюй ринок на інший, якщо потрібної ставки немає;\n• якщо коефіцієнт сильно просів — краще пропустити;\n• це аналітика, а не гарантія виграшу.",
        "no_matches": "⚠️ <b>На сьогодні не знайдено достатньо якісних матчів.</b>\n\nБот не буде публікувати слабкі або сумнівні варіанти тільки заради кількості.",
        "date_time": "🗓 <b>Дата/час:</b>",
        "league": "🏟 <b>Турнір:</b>",
        "main_bet": "✅ <b>Основна ставка:</b>",
        "safe": "🛡 <b>Обережніший варіант:</b>",
        "risky": "🔥 <b>Ризиковіший варіант:</b>",
        "winner": "📊 <b>Хто ближче до перемоги:</b>",
        "score_team": "🥅 <b>Хто ймовірніше заб’є:</b>",
        "why": "💎 <b>Чому матч у відборі:</b>",
        "analysis": "🧠 <b>Розбір:</b>",
        "numbers": "📈 <b>Коротко по цифрах:</b>",
        "disclaimer": "⚠️ <i>Це не фінансова порада. Став тільки суму, яку готовий втратити.</i>",
        "time_missing": "час не вказано джерелом",
        "league_missing": "турнір не вказано джерелом",
    },
    "en": {
        "daily_title": "🏆 <b>Football predictions for today</b>",
        "daily_intro": "The bot selected only matches where the statistical profile looks clean enough.",
        "what_to_find": "Below is exactly what to look for in the bookmaker line.",
        "count": "📌 <b>Selected matches:</b>",
        "bet": "✅ <b>Pick:</b>",
        "confidence": "🧠 <b>Confidence:</b>",
        "score": "🎯 <b>Expected score:</b>",
        "risk": "<b>Risk:</b>",
        "open_match": "Open match / check result",
        "bookmaker": "Open odds at",
        "how": "💰 <b>How to use:</b>\n• do not risk your whole bankroll on one match;\n• do not replace the market if the exact pick is unavailable;\n• if the odds dropped too much, it is better to skip;\n• this is analysis, not a guaranteed win.",
        "no_matches": "⚠️ <b>No sufficiently strong matches found for today.</b>\n\nThe bot will not post weak or questionable picks just to fill the quota.",
        "date_time": "🗓 <b>Date/time:</b>",
        "league": "🏟 <b>Competition:</b>",
        "main_bet": "✅ <b>Main pick:</b>",
        "safe": "🛡 <b>Safer option:</b>",
        "risky": "🔥 <b>Riskier option:</b>",
        "winner": "📊 <b>Who is closer to winning:</b>",
        "score_team": "🥅 <b>Who is more likely to score:</b>",
        "why": "💎 <b>Why this match passed:</b>",
        "analysis": "🧠 <b>Analysis:</b>",
        "numbers": "📈 <b>Quick stats:</b>",
        "disclaimer": "⚠️ <i>This is not financial advice. Only stake what you can afford to lose.</i>",
        "time_missing": "time was not provided by the source",
        "league_missing": "competition was not provided by the source",
    },
    "ru": {
        "daily_title": "🏆 <b>Футбольные прогнозы на сегодня</b>",
        "daily_intro": "Бот отобрал только матчи, где статистический профиль выглядит достаточно чисто.",
        "what_to_find": "Ниже — что именно искать в линии букмекера.",
        "count": "📌 <b>Матчей в отборе:</b>",
        "bet": "✅ <b>Ставка:</b>",
        "confidence": "🧠 <b>Уверенность:</b>",
        "score": "🎯 <b>Ожидаемый счёт:</b>",
        "risk": "<b>Риск:</b>",
        "open_match": "Открыть матч / проверить результат",
        "bookmaker": "Открыть линию",
        "how": "💰 <b>Как использовать:</b>\n• не ставь весь банк на один матч;\n• не заменяй рынок на другой, если нужной ставки нет;\n• если коэффициент сильно просел — лучше пропустить;\n• это аналитика, а не гарантия выигрыша.",
        "no_matches": "⚠️ <b>На сегодня не найдено достаточно качественных матчей.</b>\n\nБот не будет публиковать слабые или сомнительные варианты только ради количества.",
        "date_time": "🗓 <b>Дата/время:</b>",
        "league": "🏟 <b>Турнир:</b>",
        "main_bet": "✅ <b>Основная ставка:</b>",
        "safe": "🛡 <b>Осторожный вариант:</b>",
        "risky": "🔥 <b>Рискованный вариант:</b>",
        "winner": "📊 <b>Кто ближе к победе:</b>",
        "score_team": "🥅 <b>Кто вероятнее забьёт:</b>",
        "why": "💎 <b>Почему матч в отборе:</b>",
        "analysis": "🧠 <b>Разбор:</b>",
        "numbers": "📈 <b>Коротко по цифрам:</b>",
        "disclaimer": "⚠️ <i>Это не финансовый совет. Ставь только сумму, которую готов потерять.</i>",
        "time_missing": "время не указано источником",
        "league_missing": "турнир не указан источником",
    },
}

BET_NAMES = {
    "OVER_1_5": {"uk": "Тотал більше 1.5 гола", "en": "Over 1.5 total goals", "ru": "Тотал больше 1.5 гола"},
    "OVER_2_5": {"uk": "Тотал більше 2.5 гола", "en": "Over 2.5 total goals", "ru": "Тотал больше 2.5 гола"},
    "BTTS_YES": {"uk": "Обидві команди заб’ють — так", "en": "Both teams to score — yes", "ru": "Обе команды забьют — да"},
    "HOME_DOUBLE_CHANCE": {"uk": "Господарі не програють / 1X", "en": "Home team not to lose / 1X", "ru": "Хозяева не проиграют / 1X"},
    "AWAY_DOUBLE_CHANCE": {"uk": "Гості не програють / X2", "en": "Away team not to lose / X2", "ru": "Гости не проиграют / X2"},
    "HOME_OR_DRAW_OVER_1_5": {"uk": "1X + тотал більше 1.5", "en": "1X + over 1.5 goals", "ru": "1X + тотал больше 1.5"},
    "AWAY_OR_DRAW_OVER_1_5": {"uk": "X2 + тотал більше 1.5", "en": "X2 + over 1.5 goals", "ru": "X2 + тотал больше 1.5"},
    "HOME_DNB": {"uk": "Господарі з форою 0 / Draw No Bet", "en": "Home Draw No Bet", "ru": "Хозяева с форой 0 / Draw No Bet"},
    "AWAY_DNB": {"uk": "Гості з форою 0 / Draw No Bet", "en": "Away Draw No Bet", "ru": "Гости с форой 0 / Draw No Bet"},
}

RISK = {
    "uk": {"low": "низький", "medium": "середній", "high": "високий"},
    "en": {"low": "low", "medium": "medium", "high": "high"},
    "ru": {"low": "низкий", "medium": "средний", "high": "высокий"},
}


def L(lang: str, key: str) -> str:
    lang = normalize_lang(lang)
    return LABELS[lang][key]


def compact_reason(text: str, limit: int = 700) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def format_match_time(ctx: CandidateContext | None, lang: str = "uk") -> str:
    if not ctx:
        return L(lang, "time_missing")
    value = getattr(ctx, "start_time", "") or getattr(ctx.event, "start_time", "") or ""
    if not value:
        return L(lang, "time_missing")
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def bet_name(p: AiPick, lang: str) -> str:
    lang = normalize_lang(lang)
    if p.main_bet_code in BET_NAMES:
        return BET_NAMES[p.main_bet_code][lang]
    return str(p.main_bet_label or p.main_bet_code)


def simple_bet_name(value: str, lang: str) -> str:
    lang = normalize_lang(lang)
    raw = str(value or "")
    low = raw.lower()
    if "1.5" in raw:
        return BET_NAMES["OVER_1_5"][lang]
    if "2.5" in raw:
        return BET_NAMES["OVER_2_5"][lang]
    if "btts" in low or "об" in low or "оз" in low or "both" in low:
        return BET_NAMES["BTTS_YES"][lang]
    return html_escape(raw)


def bet_instruction(p: AiPick, lang: str) -> str:
    lang = normalize_lang(lang)
    code = p.main_bet_code
    data = {
        "uk": {
            "OVER_1_5": "У лінії букмекера шукай: <b>Тотал голів → Більше 1.5</b>.",
            "OVER_2_5": "У лінії букмекера шукай: <b>Тотал голів → Більше 2.5</b>.",
            "BTTS_YES": "Шукай ринок: <b>Обидві команди заб’ють → Так</b>.",
            "HOME_DOUBLE_CHANCE": "Шукай ринок: <b>Подвійний шанс → 1X</b>.",
            "AWAY_DOUBLE_CHANCE": "Шукай ринок: <b>Подвійний шанс → X2</b>.",
            "HOME_DNB": "Шукай: <b>господарі з форою 0</b> або <b>Draw No Bet</b>.",
            "AWAY_DNB": "Шукай: <b>гості з форою 0</b> або <b>Draw No Bet</b>.",
            "default": "Якщо такого ринку немає — краще пропустити матч, а не замінювати ставку навмання.",
        },
        "en": {
            "OVER_1_5": "In the bookmaker line, look for: <b>Total goals → Over 1.5</b>.",
            "OVER_2_5": "In the bookmaker line, look for: <b>Total goals → Over 2.5</b>.",
            "BTTS_YES": "Look for: <b>Both teams to score → Yes</b>.",
            "HOME_DOUBLE_CHANCE": "Look for: <b>Double chance → 1X</b>.",
            "AWAY_DOUBLE_CHANCE": "Look for: <b>Double chance → X2</b>.",
            "HOME_DNB": "Look for: <b>Home Draw No Bet</b>.",
            "AWAY_DNB": "Look for: <b>Away Draw No Bet</b>.",
            "default": "If this market is unavailable, it is better to skip the match instead of replacing the pick randomly.",
        },
        "ru": {
            "OVER_1_5": "В линии букмекера ищи: <b>Тотал голов → Больше 1.5</b>.",
            "OVER_2_5": "В линии букмекера ищи: <b>Тотал голов → Больше 2.5</b>.",
            "BTTS_YES": "Ищи рынок: <b>Обе команды забьют → Да</b>.",
            "HOME_DOUBLE_CHANCE": "Ищи рынок: <b>Двойной шанс → 1X</b>.",
            "AWAY_DOUBLE_CHANCE": "Ищи рынок: <b>Двойной шанс → X2</b>.",
            "HOME_DNB": "Ищи: <b>хозяева с форой 0</b> или <b>Draw No Bet</b>.",
            "AWAY_DNB": "Ищи: <b>гости с форой 0</b> или <b>Draw No Bet</b>.",
            "default": "Если такого рынка нет — лучше пропустить матч, а не заменять ставку наугад.",
        },
    }
    return data[lang].get(code, data[lang]["default"])


def risk_key(risk: str) -> str:
    value = str(risk or "").lower()
    if any(x in value for x in ["low", "низ", "низь"]):
        return "low"
    if any(x in value for x in ["high", "выс", "вис"]):
        return "high"
    return "medium"


def risk_emoji(risk: str) -> str:
    key = risk_key(risk)
    return {"low": "🟢", "medium": "🟡", "high": "🔴"}[key]


def confidence_text(confidence: int, lang: str) -> str:
    lang = normalize_lang(lang)
    if lang == "en":
        if confidence >= 75: return "high"
        if confidence >= 60: return "medium"
        if confidence >= 45: return "moderate"
        return "low"
    if lang == "ru":
        if confidence >= 75: return "высокая"
        if confidence >= 60: return "средняя"
        if confidence >= 45: return "умеренная"
        return "низкая"
    if confidence >= 75: return "висока"
    if confidence >= 60: return "середня"
    if confidence >= 45: return "помірна"
    return "низька"


def bookmaker_link_line(match_title: str, bookmaker_url: str = "", lang: str = "uk") -> str:
    settings = get_settings()
    if not settings.bookmaker_link_enabled:
        return ""
    name = settings.bookmaker_name or "bookmaker"
    url = str(bookmaker_url or "").strip()
    if not url and settings.bookmaker_search_url_template:
        url = settings.bookmaker_search_url_template.format(query=quote_plus(match_title))
    if not url:
        return ""
    return f'💵 <a href="{html_escape(url)}">{L(lang, "bookmaker")} {html_escape(name)}</a>'


def generated_why(p: AiPick, ctx: CandidateContext | None, lang: str) -> str:
    lang = normalize_lang(lang)
    if lang == "uk":
        return compact_reason(p.why_this_match_is_gold, 520)
    if not ctx:
        return {
            "en": "The match passed the filter because the selected market has a cleaner statistical profile than the match outcome.",
            "ru": "Матч прошёл фильтр, потому что выбранный рынок выглядит статистически чище, чем ставка на исход.",
        }[lang]
    h, a = ctx.home_metrics, ctx.away_metrics
    if lang == "en":
        return (
            f"The pick passed the filter because the selected market is supported by recent form data: "
            f"{ctx.home_team} have {h.wins} wins in {h.matches} recent matches, "
            f"{ctx.away_team} have {a.wins} wins in {a.matches}. "
            f"Over 1.5 profile: {h.over15}/{max(1, h.matches)} and {a.over15}/{max(1, a.matches)}."
        )
    return (
        f"Матч прошёл фильтр по статистике последних игр: "
        f"{ctx.home_team} имеет {h.wins} побед в {h.matches} матчах, "
        f"{ctx.away_team} — {a.wins} побед в {a.matches}. "
        f"Профиль ТБ 1.5: {h.over15}/{max(1, h.matches)} и {a.over15}/{max(1, a.matches)}."
    )


def generated_analysis(p: AiPick, ctx: CandidateContext | None, lang: str) -> str:
    lang = normalize_lang(lang)
    if lang == "uk":
        return compact_reason(p.reasoning, 900)
    if not ctx:
        return {
            "en": "The safest approach is to focus on the recommended market and avoid forcing a stronger outcome bet.",
            "ru": "Самый осторожный подход — играть рекомендованный рынок и не усиливать прогноз до рискованного исхода.",
        }[lang]
    h, a = ctx.home_metrics, ctx.away_metrics
    if lang == "en":
        return (
            f"The model avoids a blind outcome bet and focuses on {bet_name(p, lang)}. "
            f"Recent numbers show goals for/against {h.goals_for}:{h.goals_against} for {ctx.home_team} "
            f"and {a.goals_for}:{a.goals_against} for {ctx.away_team}. "
            f"Confidence is {p.confidence}/100, so stake sizing should remain disciplined."
        )
    return (
        f"Модель не лезет в агрессивный исход и выбирает рынок {bet_name(p, lang)}. "
        f"Последние цифры по голам: {h.goals_for}:{h.goals_against} у {ctx.home_team} "
        f"и {a.goals_for}:{a.goals_against} у {ctx.away_team}. "
        f"Уверенность {p.confidence}/100, поэтому размер ставки должен быть аккуратным."
    )


def render_daily_summary(
    picks: list[AiPick],
    rejected_summary: list[str] | None = None,
    provider: str = "",
    contexts_by_id: dict[str, CandidateContext] | None = None,
    lang: str = "uk",
) -> str:
    lang = normalize_lang(lang)
    if not picks:
        return L(lang, "no_matches")

    lines = [L(lang, "daily_title"), "", L(lang, "daily_intro"), L(lang, "what_to_find"), "", f"{L(lang, 'count')} {len(picks)}", ""]

    for idx, p in enumerate(picks, start=1):
        lines.extend([
            f"{idx}. <b>{html_escape(p.match_title)}</b>",
            f"{L(lang, 'bet')} {html_escape(bet_name(p, lang))}",
            f"{L(lang, 'confidence')} {p.confidence}/100 ({confidence_text(p.confidence, lang)})",
            f"{L(lang, 'score')} {html_escape(p.expected_score)}",
            f"{risk_emoji(p.risk_level)} {L(lang, 'risk')} {RISK[lang][risk_key(p.risk_level)]}",
        ])
        if p.tracking_url:
            lines.append(f'🔗 <a href="{html_escape(p.tracking_url)}">{L(lang, "open_match")}</a>')
        if p.bookmaker_url:
            lines.append(bookmaker_link_line(p.match_title, p.bookmaker_url, lang))
        lines.append("")

    lines.append(L(lang, "how"))
    return "\n".join(lines)


def render_pick_detail(p: AiPick, ctx: CandidateContext | None = None, lang: str = "uk") -> str:
    lang = normalize_lang(lang)
    match_time = format_match_time(ctx, lang)
    league = f"{ctx.country} • {ctx.league_name}" if ctx else L(lang, "league_missing")
    main = bet_name(p, lang)

    data_block = ""
    if ctx:
        h = ctx.home_metrics
        a = ctx.away_metrics
        data_block = (
            f"\n\n{L(lang, 'numbers')}\n"
            f"• {html_escape(ctx.home_team)}: {h.wins}-{h.draws}-{h.losses}, goals {h.goals_for}:{h.goals_against}, Over 1.5 {h.over15}/{max(1, h.matches)}, BTTS {h.btts}/{max(1, h.matches)}\n"
            f"• {html_escape(ctx.away_team)}: {a.wins}-{a.draws}-{a.losses}, goals {a.goals_for}:{a.goals_against}, Over 1.5 {a.over15}/{max(1, a.matches)}, BTTS {a.btts}/{max(1, a.matches)}"
        )

    return (
        f"⚽️ <b>{html_escape(p.match_title)}</b>\n"
        f"{L(lang, 'date_time')} {html_escape(match_time)}\n"
        f"{L(lang, 'league')} {html_escape(league)}\n\n"
        f"{L(lang, 'main_bet')} {html_escape(main)}\n"
        f"📌 {bet_instruction(p, lang)}\n\n"
        f"{L(lang, 'safe')} {simple_bet_name(p.safe_bet_label, lang)}\n"
        f"{L(lang, 'risky')} {simple_bet_name(p.risky_bet_label, lang)}\n"
        f"{risk_emoji(p.risk_level)} {L(lang, 'risk')} {RISK[lang][risk_key(p.risk_level)]}\n"
        f"{L(lang, 'confidence')} {p.confidence}/100 ({confidence_text(p.confidence, lang)})\n"
        f"{L(lang, 'score')} {html_escape(p.expected_score)}\n\n"
        f"{L(lang, 'winner')} {html_escape(localize_free_text(p.predicted_winner, lang))}\n"
        f"{L(lang, 'score_team')} {html_escape(localize_free_text(p.who_should_score, lang))}\n\n"
        f"{L(lang, 'why')}\n{html_escape(generated_why(p, ctx, lang))}\n\n"
        f"{L(lang, 'analysis')}\n{html_escape(generated_analysis(p, ctx, lang))}"
        f"{data_block}\n"
        f"🔗 <a href=\"{html_escape(p.tracking_url)}\">{L(lang, 'open_match')}</a>\n"
        f"{bookmaker_link_line(p.match_title, getattr(p, 'bookmaker_url', ''), lang)}\n\n"
        f"{L(lang, 'disclaimer')}"
    )


def localize_free_text(text: str, lang: str) -> str:
    value = str(text or "").strip()
    if lang == "uk":
        return value
    low = value.lower()
    if lang == "en":
        if "опас" in low or "ризик" in low:
            return "outcome is risky"
        if "тотал" in low:
            return "safer through total goals"
        if "хозя" in low or "госп" in low:
            return "home side is closer"
        if "гост" in low:
            return "away side is closer"
        return "market is safer than match winner"
    if "опас" in low or "ризик" in low:
        return "исход рискованный"
    if "тотал" in low:
        return "осторожнее через тотал"
    if "госп" in low or "home" in low:
        return "хозяева ближе"
    if "away" in low:
        return "гости ближе"
    return value


def render_result_line(match_title: str, score: str, bet: str, status: str, lang: str = "uk") -> str:
    lang = normalize_lang(lang)
    marks = {
        "uk": {"win": "✅ зайшло", "void": "↩️ повернення", "loss": "❌ не зайшло", "score": "Рахунок", "pick": "Прогноз"},
        "en": {"win": "✅ won", "void": "↩️ void", "loss": "❌ lost", "score": "Score", "pick": "Pick"},
        "ru": {"win": "✅ зашло", "void": "↩️ возврат", "loss": "❌ не зашло", "score": "Счёт", "pick": "Прогноз"},
    }
    m = marks[lang]
    mark = m["win"] if status == "win" else m["void"] if status == "void" else m["loss"]
    return (
        f"{mark} — <b>{html_escape(match_title)}</b>\n"
        f"{m['score']}: <b>{html_escape(score)}</b>\n"
        f"{m['pick']}: <b>{html_escape(simple_bet_name(bet, lang))}</b>"
    )
