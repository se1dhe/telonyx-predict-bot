from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from app.config import Settings, get_settings
from app.models import Prediction
from app.schemas import AiPick
from app.services.render import bet_name, simple_bet_name


logger = logging.getLogger(__name__)

_tts_semaphore = asyncio.Semaphore(1)


@dataclass(slots=True)
class VideoAsset:
    path: Path
    caption: str


async def generate_video_asset_for_prediction(prediction: Prediction) -> VideoAsset | None:
    """Generate a vertical voiced MP4 for one saved prediction."""
    settings = get_settings()
    if not settings.video_assets_enabled:
        return None

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY is empty; video asset will use fallback audio")

    ffmpeg_path = shutil.which(settings.video_assets_ffmpeg_path) or settings.video_assets_ffmpeg_path
    if not ffmpeg_path:
        logger.warning("Video asset skipped: ffmpeg not found")
        return None

    pick = parse_pick(prediction)
    asset_dir = Path(settings.video_assets_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"prediction-{prediction.id}-", dir=str(asset_dir)) as tmp:
        tmp_dir = Path(tmp)
        audio_path = tmp_dir / "voice.mp3"
        video_path = tmp_dir / "short.mp4"

        voiceover = build_voiceover_text(prediction, pick)
        await create_voiceover_audio(voiceover, audio_path, settings)
        await render_vertical_video(prediction, pick, audio_path, video_path, ffmpeg_path, settings)

        final_path = asset_dir / f"prediction-{prediction.id}-{safe_slug(match_title(prediction, pick))}.mp4"
        shutil.move(str(video_path), final_path)
        return VideoAsset(path=final_path, caption=build_video_caption(prediction, pick))


async def create_voiceover_audio(text: str, output_path: Path, settings: Settings) -> None:
    """Create voiceover audio with Gemini, cache, retry/backoff, and a no-crash fallback."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path = voiceover_cache_path(text, settings)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        logger.info("Voiceover cache hit: %s", cache_path)
        shutil.copyfile(cache_path, output_path)
        return

    async with _tts_semaphore:
        if cache_path.exists() and cache_path.stat().st_size > 0:
            logger.info("Voiceover cache hit after wait: %s", cache_path)
            shutil.copyfile(cache_path, output_path)
            return

        try:
            await create_gemini_voiceover_audio(text, output_path, settings)
        except Exception as exc:
            logger.warning("Gemini TTS failed, using fallback. error=%s", exc)
            await create_fallback_voiceover_audio(text, output_path, settings)

        if output_path.exists() and output_path.stat().st_size > 0:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output_path, cache_path)


async def create_gemini_voiceover_audio(text: str, output_path: Path, settings: Settings) -> None:
    """Create WAV voiceover using Gemini speech generation."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is empty")

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.video_assets_tts_model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            f"{settings.video_assets_tts_instructions}\n\n"
                            f"Текст озвучки:\n{text}"
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": settings.video_assets_tts_voice,
                    }
                }
            },
        },
    }
    attempts = max(1, int(settings.ai_retry_max_attempts or 3))
    timeout = aiohttp.ClientTimeout(total=max(30, int(settings.ai_timeout_seconds or 90)))
    last_error: Exception | None = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(1, attempts + 1):
            try:
                async with session.post(
                    endpoint,
                    params={"key": settings.gemini_api_key},
                    json=payload,
                ) as response:
                    raw = await response.text()
                    if response.status == 429:
                        retry_after = parse_retry_delay(raw) or float(settings.ai_retry_base_delay_seconds or 8.0) * attempt
                        retry_after = max(1.0, min(retry_after, 45.0))
                        last_error = RuntimeError(f"Gemini TTS rate limited: {raw[:1200]}")
                        if attempt >= attempts:
                            raise last_error
                        logger.warning(
                            "Gemini TTS HTTP 429 attempt=%s/%s; retry in %.1fs",
                            attempt,
                            attempts,
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if response.status >= 400:
                        raise RuntimeError(f"Gemini TTS failed status={response.status} body={raw[:1200]}")

                    break
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                delay = float(settings.ai_retry_base_delay_seconds or 8.0) * attempt
                delay = max(1.0, min(delay, 30.0))
                logger.warning(
                    "Gemini TTS error attempt=%s/%s: %s; retry in %.1fs",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        else:
            raise RuntimeError(f"Gemini TTS failed: {last_error}")

    audio_bytes, mime_type = extract_gemini_audio(raw)
    if not audio_bytes:
        raise RuntimeError(f"Gemini TTS returned no audio data: {raw[:1200]}")

    if "wav" in mime_type.lower() or audio_bytes.startswith(b"RIFF"):
        output_path.write_bytes(audio_bytes)
        return

    # Gemini can return raw PCM on some models. ffmpeg accepts WAV more reliably,
    # so wrap 24 kHz 16-bit mono PCM when no container is present.
    output_path.write_bytes(wav_wrap_pcm(audio_bytes, sample_rate=24000, channels=1, bits_per_sample=16))


async def create_fallback_voiceover_audio(text: str, output_path: Path, settings: Settings) -> None:
    """Use edge-tts when installed; otherwise create valid quiet audio so rendering never dies."""
    try:
        import edge_tts  # type: ignore

        communicate = edge_tts.Communicate(text, settings.video_assets_tts_fallback_voice)
        await communicate.save(str(output_path))
        if output_path.exists() and output_path.stat().st_size > 0:
            return
        raise RuntimeError("edge-tts produced an empty file")
    except Exception as exc:
        logger.warning("Edge TTS fallback unavailable, creating silent fallback audio: %s", exc)

    duration = max(18, min(90, math.ceil(len(text.split()) / 2.35) + 4))
    sample_rate = 24000
    silent_pcm = b"\x00\x00" * sample_rate * duration
    output_path.write_bytes(wav_wrap_pcm(silent_pcm, sample_rate=sample_rate, channels=1, bits_per_sample=16))


def voiceover_cache_path(text: str, settings: Settings) -> Path:
    voice = settings.video_assets_tts_voice or "default"
    fallback_voice = settings.video_assets_tts_fallback_voice or ""
    key = hashlib.sha256(f"{voice}:{fallback_voice}:{text}".encode("utf-8")).hexdigest()
    return Path(settings.video_assets_tts_cache_dir) / f"{key}.audio"


def extract_gemini_audio(raw_response: str) -> tuple[bytes, str]:
    data = json.loads(raw_response)
    candidates = data.get("candidates") or []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts") or []
        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data") or {}
            encoded = inline_data.get("data")
            if encoded:
                return base64.b64decode(encoded), str(inline_data.get("mimeType") or inline_data.get("mime_type") or "")
    return b"", ""


def wav_wrap_pcm(pcm: bytes, sample_rate: int, channels: int, bits_per_sample: int) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm)
    header = (
        b"RIFF"
        + (36 + data_size).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + bits_per_sample.to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
    )
    return header + pcm


def parse_retry_delay(raw: str) -> float | None:
    match = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", raw or "", flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


async def render_vertical_video(
    prediction: Prediction,
    pick: AiPick | None,
    audio_path: Path,
    output_path: Path,
    ffmpeg_path: str,
    settings: Settings,
) -> None:
    width = max(360, int(settings.video_assets_width or 1080))
    height = max(640, int(settings.video_assets_height or 1920))
    fps = max(12, int(settings.video_assets_fps or 30))
    audio_duration = await asyncio.to_thread(probe_audio_duration, audio_path, ffmpeg_path)
    duration = max(
        int(settings.video_assets_duration_seconds or 30),
        math.ceil(audio_duration + 0.8) if audio_duration else 0,
    )
    duration = max(18, min(90, duration))

    title = match_title(prediction, pick)
    title_ru = russian_video_text(title)
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = str(pick.confidence if pick else prediction.confidence)
    why = compact_text(russian_video_text((pick.why_this_match_is_gold if pick else "") or prediction.rendered_text), 150)
    time_text = format_start_time(prediction.start_time)
    odds = prediction.bookmaker_odds or (f"{pick.bookmaker_odds:.2f}" if pick and pick.bookmaker_odds else "")
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or settings.bookmaker_name

    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    local_font = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    font_file = local_font if Path(local_font).exists() else font

    pad = width // 12
    title_y = height // 7
    pick_y = height // 3
    filters = build_cinematic_background_filters(width, height, fps, duration)
    filters.extend(
        [
            f"drawbox=x={pad - 18}:y={title_y - 32}:w={width - 2 * pad + 36}:h={height // 4}:color=black@0.34:t=fill:enable='between(t,0.2,{duration})'",
            f"drawbox=x={pad - 18}:y={pick_y - 34}:w={width - 2 * pad + 36}:h={height // 4}:color=black@0.30:t=fill:enable='between(t,2.6,{duration})'",
            f"drawbox=x={pad - 18}:y={title_y - 32}:w=5:h={height // 4}:color=0x32d583@0.90:t=fill:enable='between(t,0.2,{duration})'",
            f"drawbox=x={pad - 18}:y={pick_y - 34}:w=5:h={height // 4}:color=0x8ab4ff@0.84:t=fill:enable='between(t,2.6,{duration})'",
            f"drawbox=x={pad}:y={height // 15}:w={width - 2 * pad}:h=4:color=0x32d583@0.95:t=fill",
            f"drawbox=x={pad}:y={height - 92}:w={width - 2 * pad}:h=2:color=0x8ab4ff@0.45:t=fill",
            draw_text(font_file, "TELONYX: СИГНАЛ НЕЙРОСЕТИ", scale_font(27, width), pad, height // 23, "0x32d583", alpha="if(lt(t,0.4),t/0.4,1)"),
            "fade=t=in:st=0:d=0.45",
        ]
    )
    filters.extend(draw_text_block(font_file, title_ru.upper(), scale_font(50, width), pad, title_y, "white", 18, 3, line_gap=12, start=0.35))
    filters.extend(draw_text_block(font_file, "ОСНОВНАЯ СТАВКА", scale_font(25, width), pad, pick_y, "0x8ab4ff", 28, 1, start=2.5))
    filters.extend(draw_text_block(font_file, main_bet.upper(), scale_font(43, width), pad, pick_y + scale_font(46, width), "white", 20, 2, line_gap=10, start=2.9))
    filters.extend(draw_text_block(font_file, f"УВЕРЕННОСТЬ {confidence}/100", scale_font(34, width), pad, height // 2 + 20, "0x32d583", 26, 1, start=5.2))
    filters.extend(draw_text_block(font_file, f"{bookmaker} {odds}".strip(), scale_font(28, width), pad, height // 2 + 82, "0xfed766", 26, 1, start=5.6))
    filters.extend(draw_text_block(font_file, why, scale_font(29, width), pad, height // 2 + 175, "white", 29, 4, line_gap=9, start=7.0))
    filters.extend(draw_text_block(font_file, time_text, scale_font(24, width), pad, height - 205, "0xb8c4d9", 28, 1, start=10.0))
    filters.extend(draw_text_block(font_file, "ПОЛНЫЙ РАЗБОР В ТЕЛЕГРАМЕ", scale_font(27, width), pad, height - 132, "0x32d583", 29, 1, start=11.0))
    filters.append(f"fade=t=out:st={duration - 0.7}:d=0.7")

    cmd = [
        ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        ",".join(filters),
        "-i",
        str(audio_path),
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info("Rendering video asset for prediction=%s", prediction.id)
    await asyncio.to_thread(run_ffmpeg, cmd, int(settings.video_assets_render_timeout_seconds or 120))


def build_cinematic_background_filters(width: int, height: int, fps: int, duration: int) -> list[str]:
    """Procedural vertical background: no external assets required."""
    cell = max(54, width // 11)
    glow_h = max(150, height // 7)
    lower_band_y = height - height // 4
    return [
        f"color=c=0x07111f:s={width}x{height}:r={fps}:d={duration}",
        "format=yuv420p",
        f"drawbox=x=0:y=0:w={width}:h={height}:color=0x020612@0.26:t=fill",
        f"drawgrid=width={cell}:height={cell}:thickness=1:color=0x1f6f8f@0.18",
        f"drawbox=x=0:y=0:w={width}:h={height // 3}:color=0x064b75@0.20:t=fill",
        f"drawbox=x=0:y={lower_band_y}:w={width}:h={height - lower_band_y}:color=0x063a24@0.18:t=fill",
        f"drawbox=x='mod(t*64,{width + width // 2})-{width // 2}':y={height // 11}:w={width // 2}:h=3:color=0x32d583@0.82:t=fill",
        f"drawbox=x='mod(t*39,{width + width // 3})-{width // 3}':y={height // 5}:w={width // 3}:h=2:color=0x8ab4ff@0.60:t=fill",
        f"drawbox=x='mod(t*51,{width + width // 2})-{width // 2}':y={height - height // 5}:w={width // 2}:h=3:color=0xfed766@0.50:t=fill",
        f"drawbox=x={width // 11}:y='mod(t*42,{height + 240})-240':w=2:h=240:color=0x32d583@0.24:t=fill",
        f"drawbox=x={width - width // 8}:y='mod(t*31,{height + 320})-320':w=2:h=320:color=0x8ab4ff@0.22:t=fill",
        f"drawbox=x={width // 5}:y={height // 3}:w={width - 2 * (width // 5)}:h={glow_h}:color=0x32d583@0.08:t=fill",
        f"drawbox=x={width // 7}:y={height // 2 + height // 12}:w={width - 2 * (width // 7)}:h={glow_h}:color=0x8ab4ff@0.07:t=fill",
        f"drawbox=x=0:y=0:w={width}:h={height}:color=black@0.22:t=fill",
    ]


def run_ffmpeg(cmd: list[str], timeout: int) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        safe_cmd = " ".join(shlex.quote(part) for part in cmd[:8]) + " ..."
        raise RuntimeError(f"ffmpeg failed code={result.returncode} cmd={safe_cmd} stderr={result.stderr[-1200:]}")


def probe_audio_duration(audio_path: Path, ffmpeg_path: str) -> float:
    ffprobe = str(Path(ffmpeg_path).with_name("ffprobe")) if "/" in ffmpeg_path else "ffprobe"
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            return float((result.stdout or "0").strip() or 0)
    except Exception:
        logger.warning("Failed to probe audio duration: %s", audio_path)
    return 0.0


def build_voiceover_text(prediction: Prediction, pick: AiPick | None) -> str:
    title = russian_video_text(match_title(prediction, pick))
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = pick.confidence if pick else prediction.confidence
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or get_settings().bookmaker_name
    reasoning = compact_text(russian_video_text((pick.reasoning if pick else "") or prediction.rendered_text), 360)

    return (
        f"Нейросеть разобрала матч {title}. "
        f"Источник данных: футбольная статистика и линия {bookmaker}. "
        f"Уверенность модели: {confidence} из 100. "
        f"Основная ставка: {main_bet}. "
        f"Ключевая причина: {reasoning}. "
        "Это не гарантия, а статистический фильтр. "
        "Полный разбор, рискованный вариант и ссылка на линию уже в Телеграме."
    )


def build_video_caption(prediction: Prediction, pick: AiPick | None) -> str:
    title = russian_video_text(match_title(prediction, pick))
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = pick.confidence if pick else prediction.confidence
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or get_settings().bookmaker_name
    odds = prediction.bookmaker_odds or (f"{pick.bookmaker_odds:.2f}" if pick and pick.bookmaker_odds else "")
    odds_text = f" Коэффициент: {odds}." if odds else ""
    short_description = (
        f"Нейросеть разобрала матч {title}. "
        f"Основная ставка: {main_bet}. "
        f"Уверенность модели: {confidence}/100.{odds_text} "
        f"Полный разбор и ссылка на линию {bookmaker} в Телеграме."
    )
    hashtags = "#ставки #футбол #прогноз #спорт #беттинг #телеграм"

    return "\n".join(
        [
            "Что вставить в ТикТок:",
            short_description,
            hashtags,
            "",
            "Что вставить в Ютуб Шортс:",
            f"{title} | прогноз нейросети",
            short_description,
            hashtags,
        ]
    )


def russian_video_text(value: str) -> str:
    """Normalize common Ukrainian AI phrases so generated videos stay Russian-only."""
    text = str(value or "")
    replacements = {
        "Болгарія": "Болгария",
        "Молдова пропускає": "Молдова пропускает",
        "Азербайджан виглядає": "Азербайджан выглядит",
        "Болгарія виглядає": "Болгария выглядит",
        "Обидві команди": "Обе команды",
        "обидві команди": "обе команды",
        "виглядає": "выглядит",
        "сильнішою": "сильнее",
        "фаворитом": "фаворитом",
        "але результат ризиковий": "но результат рискованный",
        "можуть забити": "могут забить",
        "краще через тотал": "лучше через тотал",
        "але": "но",
        "Висока ймовірність": "Высокая вероятность",
        "висока ймовірність": "высокая вероятность",
        "тоталу більше": "тотала больше",
        "тотал більше": "тотал больше",
        "через слабку оборону": "из-за слабой обороны",
        "слабку оборону": "слабую оборону",
        "та схильність": "и склонность",
        "схильність": "склонность",
        "Молдови": "Молдовы",
        "обох команд": "обеих команд",
        "до результативних матчів": "к результативным матчам",
        "демонструють": "показывают",
        "високу результативність": "высокую результативность",
        "у своїх останніх матчах": "в своих последних матчах",
        "часто пробиваючи": "часто пробивая",
        "у середньому": "в среднем",
        "понад": "больше",
        "голи": "голы",
        "голів": "голов",
        "матчів": "матчей",
        "ігор": "игр",
        "всі": "все",
        "останніх": "последних",
        "завершилися": "завершились",
        "також": "тоже",
        "результативні": "результативные",
        "забиваючи та пропускаючи": "забивая и пропуская",
        "товариський матч": "товарищеский матч",
        "статистика голів": "статистика голов",
        "дуже переконлива": "очень убедительная",
        "мають": "имеют",
        "схожу статистику": "похожую статистику",
        "регулярно пропускають": "регулярно пропускают",
        "грають матчі": "играют матчи",
        "тенденція до голів очевидна": "тенденция к голам очевидна",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def match_title(prediction: Prediction, pick: AiPick | None) -> str:
    if pick and pick.match_title:
        return pick.match_title
    return f"{prediction.home_team} — {prediction.away_team}".strip(" —") or f"prediction-{prediction.id}"


def parse_pick(prediction: Prediction) -> AiPick | None:
    try:
        if not prediction.prediction_json:
            return None
        return AiPick.model_validate_json(prediction.prediction_json)
    except Exception:
        logger.exception("Failed to parse prediction_json for video asset prediction=%s", prediction.id)
        return None


def compact_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def format_start_time(value: str) -> str:
    if not value:
        return "время не указано"
    settings = get_settings()
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(settings.tz)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "video"


def draw_text(
    font_file: str,
    text: str,
    size: int,
    x: int | str,
    y: int | str,
    color: str,
    line_spacing: int = 10,
    alpha: str | None = None,
    enable: str | None = None,
) -> str:
    escaped = escape_drawtext(text)
    value = (
        "drawtext="
        f"fontfile='{escape_drawtext(font_file)}':"
        f"text='{escaped}':"
        f"fontsize={size}:"
        f"fontcolor={color}:"
        f"x={x}:y={y}:"
        f"line_spacing={line_spacing}:"
        "shadowcolor=black@0.55:shadowx=3:shadowy=3"
    )
    if alpha:
        value += f":alpha='{alpha}'"
    if enable:
        value += f":enable='{enable}'"
    return value


def draw_text_block(
    font_file: str,
    text: str,
    size: int,
    x: int,
    y: int,
    color: str,
    max_chars: int,
    max_lines: int,
    line_gap: int = 8,
    start: float = 0.0,
) -> list[str]:
    lines = wrap_lines(text, max_chars)[:max_lines]
    line_height = int(size * 1.16) + line_gap
    result: list[str] = []
    for index, line in enumerate(lines):
        line_start = start + index * 0.08
        alpha = f"if(lt(t,{line_start}),0,if(lt(t,{line_start + 0.35}),(t-{line_start})/0.35,1))"
        result.append(
            draw_text(
                font_file,
                line,
                size,
                x,
                y + index * line_height,
                color,
                alpha=alpha,
                enable=f"gte(t,{line_start})",
            )
        )
    return result


def scale_font(size: int, width: int) -> int:
    return max(18, int(size * width / 720))


def escape_drawtext(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def wrap_lines(value: str, width: int) -> list[str]:
    words = str(value or "").split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
