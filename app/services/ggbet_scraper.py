from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class GGBetEvent:
    home_team: str
    away_team: str
    start_time: datetime | None
    url: str
    over15_odds: float

    @property
    def title(self) -> str:
        return f"{self.home_team} — {self.away_team}"


class GGBetScraper:
    """Browser-based GGBET collector for real match URLs and Total Over 1.5 odds."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def events_by_date(self, target_date: date) -> list[GGBetEvent]:
        return await asyncio.to_thread(self._events_by_date_sync, target_date)

    def _events_by_date_sync(self, target_date: date) -> list[GGBetEvent]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            logger.warning("GGBET scraper unavailable: playwright is not installed: %s", exc)
            return []

        base_url = "https://ggbet.ua"
        source_url = self.settings.ggbet_football_url
        max_links = max(1, int(self.settings.ggbet_scraper_match_limit or self.settings.ggbet_scraper_limit or 80))
        timeout = max(10000, int(self.settings.ggbet_scraper_timeout_ms or 60000))
        target_tz = safe_zoneinfo(self.settings.tz)
        min_odds = float(self.settings.ggbet_scraper_min_odds or self.settings.min_pick_odds or 1.3)
        events: list[GGBetEvent] = []

        try:
            with sync_playwright() as p:
                proxy = build_playwright_proxy(
                    self.settings.ggbet_proxy_server,
                    self.settings.ggbet_proxy_username,
                    self.settings.ggbet_proxy_password,
                )
                if proxy:
                    logger.info("GGBET scraper: using configured proxy server=%s", proxy.get("server"))
                browser = p.chromium.launch(
                    headless=bool(self.settings.ggbet_scraper_headless),
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                    locale="en-US",
                    timezone_id=self.settings.tz,
                    viewport={"width": 1280, "height": 900},
                    proxy=proxy,
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                page = context.new_page()
                page.set_default_timeout(timeout)

                logger.info("GGBET scraper: opening %s", source_url)
                page.goto(source_url, wait_until="networkidle", timeout=timeout)
                page.wait_for_timeout(2500)
                for _ in range(5):
                    page.evaluate("window.scrollBy(0, 1200)")
                    page.wait_for_timeout(700)

                links = page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('a[href*="/match/"]'))
                      .map(a => a.getAttribute('href'))
                      .filter(Boolean)
                    """
                )
                unique_links = unique_items([str(link) for link in links])
                filtered_links = [
                    link for link in unique_links
                    if link_matches_target_date(link, target_date, target_tz)
                ][:max_links]
                logger.info("GGBET scraper: match links target_date=%s count=%s", target_date, len(filtered_links))
                if not filtered_links:
                    log_empty_links_diagnostics(page)

                for index, link in enumerate(filtered_links, start=1):
                    match_url = link if link.startswith("http") else f"{base_url}{link}"
                    try:
                        page.goto(match_url, wait_until="networkidle", timeout=min(timeout, 25000))
                        page.wait_for_timeout(900)
                        text = page.locator("body").inner_text(timeout=5000)
                    except Exception as exc:
                        logger.info("GGBET scraper: failed match page %s: %s", match_url, exc)
                        continue

                    event = parse_match_page(match_url, text, target_tz)
                    if not event:
                        continue
                    if event.start_time and event.start_time.date() != target_date:
                        continue
                    if event.over15_odds < min_odds:
                        continue
                    events.append(event)
                    if len(events) >= max(1, int(self.settings.ggbet_scraper_limit or 80)):
                        break
                    logger.debug("GGBET scraper: accepted %s/%s %s %.2f", index, len(filtered_links), event.title, event.over15_odds)

                browser.close()
        except Exception as exc:
            logger.warning("GGBET scraper failed: %s", exc)
            return events

        events.sort(key=lambda event: event.start_time or datetime.max.replace(tzinfo=target_tz))
        logger.info("GGBET scraper: accepted events=%s target_date=%s min_odds=%.2f", len(events), target_date, min_odds)
        return events


def log_empty_links_diagnostics(page: object) -> None:
    try:
        current_url = page.url
        title = page.title()
        anchor_sample = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a'))
              .slice(0, 20)
              .map(a => a.getAttribute('href') || a.textContent || '')
              .filter(Boolean)
            """
        )
        body_text = page.locator("body").inner_text(timeout=5000)
        preview = re.sub(r"\s+", " ", str(body_text or ""))[:700]
        logger.warning(
            "GGBET scraper: no match links diagnostics url=%s title=%r anchors=%s body=%r",
            current_url,
            title,
            anchor_sample,
            preview,
        )
    except Exception as exc:
        logger.warning("GGBET scraper: failed no-link diagnostics: %s", exc)


def build_playwright_proxy(server: str, username: str = "", password: str = "") -> dict[str, str] | None:
    value = str(server or "").strip()
    if not value:
        return None
    proxy: dict[str, str] = {"server": value}
    if username:
        proxy["username"] = str(username)
    if password:
        proxy["password"] = str(password)
    return proxy


def parse_match_page(url: str, text: str, tz: ZoneInfo) -> GGBetEvent | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return None

    odds = extract_total_over_15(lines)
    if odds is None:
        return None

    home, away = teams_from_slug(url)
    if not home or not away:
        home, away = teams_from_lines(lines)
    if not home or not away:
        return None

    return GGBetEvent(
        home_team=home,
        away_team=away,
        start_time=extract_start_time(lines, tz),
        url=url,
        over15_odds=odds,
    )


def extract_total_over_15(lines: list[str]) -> float | None:
    stop_markets = {
        "handicap",
        "next goal",
        "both teams to score",
        "draw no bet",
        "double chance",
        "correct score",
    }
    for index, line in enumerate(lines):
        if normalize_line(line) != "total":
            continue
        for item_index in range(index + 1, min(index + 80, len(lines) - 1)):
            current = normalize_line(lines[item_index])
            if current in stop_markets:
                break
            if current == "over 1.5":
                odd = parse_odd(lines[item_index + 1])
                if odd:
                    return odd
                break
    return None


def extract_start_time(lines: list[str], tz: ZoneInfo) -> datetime | None:
    pattern = re.compile(r"^\d{2}:\d{2}$")
    now = datetime.now(tz)
    for index, line in enumerate(lines):
        if not pattern.match(line) or index + 1 >= len(lines):
            continue
        day_label = lines[index + 1]
        day = parse_day_label(day_label, now)
        if not day:
            continue
        hour, minute = [int(part) for part in line.split(":", 1)]
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    return None


def parse_day_label(value: str, now: datetime) -> date | None:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered == "today":
        return now.date()
    if lowered == "tomorrow":
        return (now + timedelta(days=1)).date()
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    match = re.match(r"^([A-Za-z]+)\s+(\d{1,2})$", raw)
    if match:
        try:
            parsed = datetime.strptime(f"{match.group(1)} {match.group(2)} {now.year}", "%B %d %Y")
            return parsed.date()
        except ValueError:
            return None
    return None


def teams_from_slug(url: str) -> tuple[str, str]:
    slug = str(url).rstrip("/").split("/")[-1]
    if "-vs-" not in slug or ":" in slug:
        return "", ""
    home_raw, away_raw = slug.split("-vs-", 1)
    away_parts = away_raw.split("-")
    if len(away_parts) > 2 and away_parts[-1].isdigit() and away_parts[-2].isdigit():
        away_raw = "-".join(away_parts[:-2])
    return title_from_slug(home_raw), title_from_slug(away_raw)


def teams_from_lines(lines: list[str]) -> tuple[str, str]:
    time_index = next((i for i, line in enumerate(lines) if re.match(r"^\d{2}:\d{2}$", line)), -1)
    if time_index >= 0 and time_index + 3 < len(lines):
        return lines[time_index + 2], lines[time_index + 3]
    return "", ""


def title_from_slug(value: str) -> str:
    return " ".join(part for part in str(value).replace("-", " ").title().split() if part)


def link_matches_target_date(link: str, target_date: date, tz: ZoneInfo) -> bool:
    slug = str(link).rstrip("/").split("/")[-1]
    suffix = f"{target_date.day:02d}-{target_date.month:02d}"
    if suffix in slug:
        return True
    parts = slug.split("-")
    has_date_suffix = len(parts) >= 2 and parts[-1].isdigit() and parts[-2].isdigit()
    if has_date_suffix:
        return False
    # UUID/live links have no date in slug; keep them only for today's run.
    return target_date == datetime.now(tz).date()


def ggbet_event_to_odds(event: GGBetEvent) -> list[dict[str, object]]:
    return [
        {
            "bookmaker": "GGBET",
            "market": "Total",
            "values": [{"value": "Over 1.5", "odd": f"{event.over15_odds:.2f}"}],
        }
    ]


def normalize_for_match(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\b(fc|cf|sc|afc|ac|club|football|soccer|res|u20|u21)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def teams_match(left: str, right: str) -> bool:
    left_norm = normalize_for_match(left)
    right_norm = normalize_for_match(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
        return True
    left_tokens = {token for token in left_norm.split() if len(token) >= 3}
    right_tokens = {token for token in right_norm.split() if len(token) >= 3}
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) >= min(2, len(left_tokens), len(right_tokens))


def parse_odd(raw: object) -> float | None:
    try:
        value = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return value if value > 1 else None


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def unique_items(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def safe_zoneinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Kiev")
    except Exception:
        return ZoneInfo("Europe/Kiev")
