from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.i18n import normalize_lang
from app.schemas import AiPick, CandidateContext


def html_escape(value: object) -> str:
    """Минимальное HTML-экранирование для Telegram parse_mode=HTML."""
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
        "risk": "<b>Ризик:</b>",
        "open_match": "Відкрити матч / перевірити результат",
        "bookmaker": "Відкрити лінію",
        "bookmaker_odds": "💵 <b>Кеф / лінія:</b>",
        "how": "💰 <b>Як використовувати:</b>\n• не став увесь банк на один матч;\n• не замінюй ринок на інший, якщо потрібної ставки немає;\n• якщо коефіцієнт сильно просів — краще пропустити;\n• це аналітика, а не гарантія виграшу.",
        "no_matches": "⚠️ <b>На сьогодні не знайдено достатньо якісних матчів.</b>\n\nБот не буде публікувати слабкі або сумнівні варіанти тільки заради кількості.",
        "date_time": "🗓 <b>Дата/час (Київ):</b>",
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
        "goals": "голи",
        "finished": "📌 Матч завершено",
        "won": "✅ зайшло",
        "lost": "❌ не зайшло",
        "void": "↩️ повернення",
        "prediction": "Прогноз",
    },
    "en": {
        "daily_title": "🏆 <b>Football predictions for today</b>",
        "daily_intro": "The bot selected only matches where the statistical profile looks clean enough.",
        "what_to_find": "Below is exactly what to look for in the bookmaker line.",
        "count": "📌 <b>Selected matches:</b>",
        "bet": "✅ <b>Pick:</b>",
        "confidence": "🧠 <b>Confidence:</b>",
        "risk": "<b>Risk:</b>",
        "open_match": "Open match / check result",
        "bookmaker": "Open odds at",
        "bookmaker_odds": "💵 <b>Odds / line:</b>",
        "how": "💰 <b>How to use:</b>\n• do not risk your whole bankroll on one match;\n• do not replace the market if the exact pick is unavailable;\n• if the odds dropped too much, it is better to skip;\n• this is analysis, not a guaranteed win.",
        "no_matches": "⚠️ <b>No sufficiently strong matches found for today.</b>\n\nThe bot will not post weak or questionable picks just to fill the quota.",
        "date_time": "🗓 <b>Date/time (Kyiv):</b>",
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
        "goals": "goals",
        "finished": "📌 Match finished",
        "won": "✅ won",
        "lost": "❌ lost",
        "void": "↩️ void",
        "prediction": "Pick",
    },
    "ru": {
        "daily_title": "🏆 <b>Футбольные прогнозы на сегодня</b>",
        "daily_intro": "Бот отобрал только матчи, где статистический профиль выглядит достаточно чисто.",
        "what_to_find": "Ниже — что именно искать в линии букмекера.",
        "count": "📌 <b>Матчей в отборе:</b>",
        "bet": "✅ <b>Ставка:</b>",
        "confidence": "🧠 <b>Уверенность:</b>",
        "risk": "<b>Риск:</b>",
        "open_match": "Открыть матч / проверить результат",
        "bookmaker": "Открыть линию",
        "bookmaker_odds": "💵 <b>Кеф / линия:</b>",
        "how": "💰 <b>Как использовать:</b>\n• не ставь весь банк на один матч;\n• не заменяй рынок на другой, если нужной ставки нет;\n• если коэффициент сильно просел — лучше пропустить;\n• это аналитика, а не гарантия выигрыша.",
        "no_matches": "⚠️ <b>На сегодня не найдено достаточно качественных матчей.</b>\n\nБот не будет публиковать слабые или сомнительные варианты только ради количества.",
        "date_time": "🗓 <b>Дата/время (Киев):</b>",
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
        "goals": "голы",
        "finished": "📌 Матч завершён",
        "won": "✅ зашло",
        "lost": "❌ не зашло",
        "void": "↩️ возврат",
        "prediction": "Прогноз",
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


def localize_free_text(text: str, lang: str) -> str:
    """Оставить произвольный текст как есть, но безопасно для HTML."""
    return str(text or "").strip()


def format_match_time(ctx: CandidateContext | None, lang: str = "uk") -> str:
    """Вернуть время матча в таймзоне проекта."""
    if not ctx:
        return L(lang, "time_missing")

    value = getattr(ctx, "start_time", "") or getattr(ctx, "event", None) and getattr(ctx.event, "start_time", "") or ""
    if not value:
        return L(lang, "time_missing")

    try:
        raw = str(value).strip()
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        target_tz_name = getattr(get_settings(), "tz", "Europe/Kiev") or "Europe/Kiev"
        try:
            target_tz = ZoneInfo(target_tz_name)
        except Exception:
            target_tz = ZoneInfo("Europe/Kiev")

        return dt.astimezone(target_tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def bet_name(p: AiPick, lang: str) -> str:
    lang = normalize_lang(lang)
    if p.main_bet_code in BET_NAMES:
        return BET_NAMES[p.main_bet_code][lang]
    return simple_bet_name(str(p.main_bet_label or p.main_bet_code), lang)


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

    return html_escape(localize_free_text(raw, lang))


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
    return {"low": "🟢", "medium": "🟡", "high": "🔴"}[risk_key(risk)]


def confidence_text(confidence: int, lang: str) -> str:
    lang = normalize_lang(lang)
    if lang == "en":
        if confidence >= 75:
            return "high"
        if confidence >= 60:
            return "medium"
        if confidence >= 45:
            return "moderate"
        return "low"

    if lang == "ru":
        if confidence >= 75:
            return "высокая"
        if confidence >= 60:
            return "средняя"
        if confidence >= 45:
            return "умеренная"
        return "низкая"

    if confidence >= 75:
        return "висока"
    if confidence >= 60:
        return "середня"
    if confidence >= 45:
        return "помірна"
    return "низька"


def bookmaker_link_line(match_title: str, bookmaker_url: str = "", lang: str = "uk", bookmaker_name: str = "", odds: float | None = None, match_time: str = "") -> str:
    settings = get_settings()
    if not settings.bookmaker_link_enabled:
        return ""

    name = bookmaker_name or settings.bookmaker_name or "bookmaker"
    url = str(bookmaker_url or "").strip()

    if not url and (name or "").strip().lower() == "ggbet":
        url = build_ggbet_match_url_from_title(match_title, match_time)

    if not url:
        return ""

    odds_text = f" • {float(odds):.2f}" if odds else ""
    return f'💵 <a href="{html_escape(url)}">{L(lang, "bookmaker")} {html_escape(name)}{html_escape(odds_text)}</a>'


def bookmaker_odds_line(p: AiPick, lang: str) -> str:
    name = p.bookmaker_name or get_settings().bookmaker_name or ""
    if not name and not p.bookmaker_odds:
        return ""
    odds = f"{float(p.bookmaker_odds):.2f}" if p.bookmaker_odds else "—"
    return f"{L(lang, 'bookmaker_odds')} {html_escape(odds)}"


def build_ggbet_match_url_from_title(match_title: str, match_time: str = "") -> str:
    home, away = split_match_title(match_title)
    if not home or not away:
        return ""

    slug = f"{slugify_url_part(home)}-vs-{slugify_url_part(away)}"
    parsed = parse_match_datetime(match_time)
    if parsed:
        slug = f"{slug}-{parsed.strftime('%d-%m')}"
    return f"https://ggbet.ua/uk-ua/sports/match/{slug}"


def split_match_title(match_title: str) -> tuple[str, str]:
    if "—" in match_title:
        home, away = match_title.split("—", 1)
        return home.strip(), away.strip()
    if " vs " in match_title.lower():
        parts = re.split(r"\s+vs\s+", match_title, flags=re.IGNORECASE)
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()
    return match_title.strip(), ""


def slugify_url_part(value: str) -> str:
    text = str(value or "").lower().strip()
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("&", " and ")
    text = re.sub(r"\b(club|football|soccer)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    slug = re.sub(r"-+", "-", text).strip("-") or "team"
    return GGBET_TEAM_SLUG_ALIASES.get(slug, slug)


GGBET_TEAM_SLUG_ALIASES = {
    # GGBET keeps the Dutch club prefix for this team; API-Football usually does not.
    "ijsselmeervogels": "vv-ijsselmeervogels",
}


def parse_match_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if " " in raw and "T" not in raw:
            return datetime.fromisoformat(raw.replace(" ", "T"))
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def generated_why(p: AiPick, ctx: CandidateContext | None, lang: str) -> str:
    if p.why_this_match_is_gold:
        return compact_reason(p.why_this_match_is_gold, 520)

    lang = normalize_lang(lang)
    return {
        "uk": "Матч пройшов фільтр, тому що рекомендований ринок виглядає статистично чистіше, ніж ставка на переможця.",
        "en": "The match passed the filter because the selected market has a cleaner statistical profile than the match outcome.",
        "ru": "Матч прошёл фильтр, потому что выбранный рынок выглядит статистически чище, чем ставка на исход.",
    }[lang]


def generated_analysis(p: AiPick, ctx: CandidateContext | None, lang: str) -> str:
    if p.reasoning:
        return compact_reason(p.reasoning, 900)

    lang = normalize_lang(lang)
    return {
        "uk": "Найобережніший підхід — грати рекомендований ринок і не посилювати прогноз до ризикового результату.",
        "en": "The safest approach is to focus on the recommended market and avoid forcing a stronger outcome bet.",
        "ru": "Самый осторожный подход — играть рекомендованный рынок и не усиливать прогноз до рискованного исхода.",
    }[lang]


def render_daily_summary(
    picks: list[AiPick],
    rejected_summary: list[str] | None = None,
    provider: str = "",
    contexts_by_id: dict[str, CandidateContext] | None = None,
    lang: str = "uk",
) -> str:
    """Сводный пост на день без ожидаемого счёта."""
    lang = normalize_lang(lang)
    if not picks:
        return L(lang, "no_matches")

    lines = [
        L(lang, "daily_title"),
        "",
        L(lang, "daily_intro"),
        L(lang, "what_to_find"),
        "",
        f"{L(lang, 'count')} {len(picks)}",
        "",
    ]

    for idx, p in enumerate(picks, start=1):
        lines.extend([
            f"{idx}. <b>{html_escape(p.match_title)}</b>",
            f"{L(lang, 'bet')} {html_escape(bet_name(p, lang))}",
            f"{L(lang, 'confidence')} {p.confidence}/100 ({confidence_text(p.confidence, lang)})",
            f"{risk_emoji(p.risk_level)} {L(lang, 'risk')} {RISK[lang][risk_key(p.risk_level)]}",
        ])

        odds_line = bookmaker_odds_line(p, lang)
        if odds_line:
            lines.append(odds_line)

        ctx = contexts_by_id.get(p.fixture_id) if contexts_by_id else None
        line = bookmaker_link_line(
            p.match_title,
            p.bookmaker_url,
            lang,
            p.bookmaker_name,
            p.bookmaker_odds,
            ctx.start_time if ctx else "",
        )
        if line:
            lines.append(line)

        lines.append("")

    lines.append(L(lang, "how"))
    return "\n".join(lines)


def render_pick_detail(p: AiPick, ctx: CandidateContext | None = None, lang: str = "uk") -> str:
    """Детальный пост по одному прогнозу без ожидаемого счёта."""
    lang = normalize_lang(lang)
    match_time = format_match_time(ctx, lang)
    league = f"{ctx.country} • {ctx.league_name}" if ctx else L(lang, "league_missing")
    main = bet_name(p, lang)

    data_block = ""
    if ctx:
        h = ctx.home_metrics
        a = ctx.away_metrics
        goals_label = L(lang, "goals")
        total15_label = "ТБ 1.5" if lang in {"uk", "ru"} else "Over 1.5"
        btts_label = "ОЗ" if lang in {"uk", "ru"} else "BTTS"
        data_block = (
            f"\n\n{L(lang, 'numbers')}\n"
            f"• {html_escape(ctx.home_team)}: {h.wins}-{h.draws}-{h.losses}, {goals_label} {h.goals_for}:{h.goals_against}, {total15_label} {h.over15}/{max(1, h.matches)}, {btts_label} {h.btts}/{max(1, h.matches)}\n"
            f"• {html_escape(ctx.away_team)}: {a.wins}-{a.draws}-{a.losses}, {goals_label} {a.goals_for}:{a.goals_against}, {total15_label} {a.over15}/{max(1, a.matches)}, {btts_label} {a.btts}/{max(1, a.matches)}"
        )

    lines = [
        f"⚽️ <b>{html_escape(p.match_title)}</b>",
        f"{L(lang, 'date_time')} {html_escape(match_time)}",
        f"{L(lang, 'league')} {html_escape(league)}",
        "",
        f"{L(lang, 'main_bet')} {html_escape(main)}",
        bet_instruction(p, lang),
        bookmaker_odds_line(p, lang),
        f"{L(lang, 'confidence')} {p.confidence}/100 ({confidence_text(p.confidence, lang)})",
        f"{risk_emoji(p.risk_level)} {L(lang, 'risk')} {RISK[lang][risk_key(p.risk_level)]}",
        "",
        f"{L(lang, 'safe')} {html_escape(simple_bet_name(p.safe_bet_label, lang))}",
        f"{L(lang, 'risky')} {html_escape(simple_bet_name(p.risky_bet_label, lang))}",
        "",
        f"{L(lang, 'winner')} {html_escape(localize_free_text(p.predicted_winner, lang))}",
        f"{L(lang, 'score_team')} {html_escape(localize_free_text(p.who_should_score, lang))}",
        "",
        f"{L(lang, 'why')}\n{html_escape(generated_why(p, ctx, lang))}",
        "",
        f"{L(lang, 'analysis')}\n{html_escape(generated_analysis(p, ctx, lang))}{data_block}",
    ]

    line = bookmaker_link_line(
        p.match_title,
        p.bookmaker_url,
        lang,
        p.bookmaker_name,
        p.bookmaker_odds,
        ctx.start_time if ctx else "",
    )
    if line:
        lines.append("")
        lines.append(line)

    lines.append("")
    lines.append(L(lang, "disclaimer"))
    return "\n".join(lines)


def render_result_message(
    prediction: object,
    status: str,
    lang: str = "uk",
) -> str:
    """Пост результата без строки финального счёта.

    Счёт больше не показываем пользователю. Он может использоваться только
    внутри result checker для расчёта рынков ТБ/ОЗ/двойной шанс/DNB.
    """
    lang = normalize_lang(lang)
    status_key = {"win": "won", "loss": "lost", "void": "void"}.get(status, "void")

    match_title = f"{getattr(prediction, 'home_team', '')} — {getattr(prediction, 'away_team', '')}".strip(" —")
    bet_label = getattr(prediction, "main_bet_label", "") or getattr(prediction, "main_bet_code", "")

    return (
        f"{L(lang, 'finished')}\n\n"
        f"{L(lang, status_key)} — {html_escape(match_title)}\n"
        f"{L(lang, 'prediction')}: {html_escape(simple_bet_name(str(bet_label), lang))}"
    )


def render_result_line(*args: object, lang: str = "uk") -> str:
    """Совместимый рендер результата без строки счёта.

    Поддерживает два старых варианта вызова:
    1) render_result_line(prediction, status, lang="uk")
    2) render_result_line(match_title, score, bet_label, status, lang="uk")

    Аргумент score намеренно игнорируется, чтобы больше не публиковать счёт.
    """
    if len(args) >= 4:
        match_title = str(args[0] or "")
        bet_label = str(args[2] or "")
        status = str(args[3] or "void")
        status_key = {"win": "won", "loss": "lost", "void": "void"}.get(status, "void")
        return (
            f"{L(lang, status_key)} — {html_escape(match_title)}\n"
            f"{L(lang, 'prediction')}: {html_escape(simple_bet_name(bet_label, lang))}"
        )

    if len(args) >= 2:
        return render_result_message(prediction=args[0], status=str(args[1] or "void"), lang=lang)

    return ""
