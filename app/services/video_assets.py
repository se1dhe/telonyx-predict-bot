from __future__ import annotations

import asyncio
import base64
import logging
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
        logger.warning("Video asset skipped: GEMINI_API_KEY is empty")
        return None

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
    """Create WAV voiceover using Gemini speech generation."""
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
    timeout = aiohttp.ClientTimeout(total=max(30, int(settings.ai_timeout_seconds or 90)))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            endpoint,
            params={"key": settings.gemini_api_key},
            json=payload,
        ) as response:
            raw = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Gemini TTS failed status={response.status} body={raw[:1200]}")

    audio_bytes, mime_type = extract_gemini_audio(raw)
    if not audio_bytes:
        raise RuntimeError(f"Gemini TTS returned no audio data: {raw[:1200]}")

    if "wav" in mime_type.lower() or audio_bytes.startswith(b"RIFF"):
        output_path.write_bytes(audio_bytes)
        return

    # Gemini can return raw PCM on some models. ffmpeg accepts WAV more reliably,
    # so wrap 24 kHz 16-bit mono PCM when no container is present.
    output_path.write_bytes(wav_wrap_pcm(audio_bytes, sample_rate=24000, channels=1, bits_per_sample=16))


def extract_gemini_audio(raw_response: str) -> tuple[bytes, str]:
    import json

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
    duration = max(20, min(60, int(settings.video_assets_duration_seconds or 38)))

    title = match_title(prediction, pick)
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = str(pick.confidence if pick else prediction.confidence)
    why = compact_text((pick.why_this_match_is_gold if pick else "") or prediction.rendered_text, 95)
    time_text = format_start_time(prediction.start_time)
    odds = prediction.bookmaker_odds or (f"{pick.bookmaker_odds:.2f}" if pick and pick.bookmaker_odds else "")
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or settings.bookmaker_name

    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    local_font = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    font_file = local_font if Path(local_font).exists() else font

    filters = [
        f"color=c=0x08111f:s={width}x{height}:r={fps}:d={duration}",
        "format=yuv420p",
        f"drawbox=x=0:y=0:w={width}:h={height}:color=0x020712@0.45:t=fill",
        f"drawbox=x=0:y=0:w={width}:h={max(80, height // 12)}:color=0x123d5a@0.55:t=fill",
        f"drawbox=x=0:y={height - max(120, height // 10)}:w={width}:h={max(120, height // 10)}:color=0x07351f@0.45:t=fill",
        f"drawbox=x={width // 12}:y={height // 14}:w={width - width // 6}:h=4:color=0x32d583@0.95:t=fill",
        draw_text(font_file, "AI MATCH SIGNAL", scale_font(38, width), width // 12, height // 18, "0x32d583"),
        draw_text(font_file, wrap_text(title.upper(), 20), scale_font(52, width), width // 12, height // 7, "white", line_spacing=16),
        draw_text(font_file, "MAIN PICK", scale_font(30, width), width // 12, height // 3, "0x8ab4ff"),
        draw_text(font_file, wrap_text(main_bet.upper(), 20), scale_font(48, width), width // 12, height // 3 + 52, "white", line_spacing=14),
        draw_text(font_file, f"CONFIDENCE {confidence}/100", scale_font(38, width), width // 12, height // 2, "0x32d583"),
        draw_text(font_file, wrap_text(f"{bookmaker} {odds}".strip(), 24), scale_font(30, width), width // 12, height // 2 + 66, "0xfed766"),
        draw_text(font_file, wrap_text(why, 27), scale_font(32, width), width // 12, height // 2 + 160, "white", line_spacing=12),
        draw_text(font_file, wrap_text(time_text, 26), scale_font(26, width), width // 12, height - 205, "0xb8c4d9"),
        draw_text(font_file, "FULL BREAKDOWN IN TELEGRAM", scale_font(30, width), width // 12, height - 130, "0x32d583"),
        "fade=t=in:st=0:d=0.45",
        f"fade=t=out:st={duration - 0.7}:d=0.7",
    ]

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
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    logger.info("Rendering video asset for prediction=%s", prediction.id)
    await asyncio.to_thread(run_ffmpeg, cmd, int(settings.video_assets_render_timeout_seconds or 120))


def run_ffmpeg(cmd: list[str], timeout: int) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        safe_cmd = " ".join(shlex.quote(part) for part in cmd[:8]) + " ..."
        raise RuntimeError(f"ffmpeg failed code={result.returncode} cmd={safe_cmd} stderr={result.stderr[-1200:]}")


def build_voiceover_text(prediction: Prediction, pick: AiPick | None) -> str:
    title = match_title(prediction, pick)
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = pick.confidence if pick else prediction.confidence
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or get_settings().bookmaker_name
    reasoning = compact_text((pick.reasoning if pick else "") or prediction.rendered_text, 360)

    return (
        f"Нейросеть разобрала матч {title}. "
        f"Источник данных: API Football и линия {bookmaker}. "
        f"Уверенность модели: {confidence} из 100. "
        f"Основная ставка: {main_bet}. "
        f"Ключевая причина: {reasoning}. "
        "Это не гарантия, а статистический фильтр. "
        "Полный разбор, рискованный вариант и ссылка на линию уже в Telegram."
    )


def build_video_caption(prediction: Prediction, pick: AiPick | None) -> str:
    title = match_title(prediction, pick)
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = pick.confidence if pick else prediction.confidence
    return f"🎥 Видео для Shorts/TikTok\n⚽️ {title}\n🎯 {main_bet}\nAI confidence: {confidence}/100"


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
    x: int,
    y: int,
    color: str,
    line_spacing: int = 10,
) -> str:
    escaped = escape_drawtext(text)
    return (
        "drawtext="
        f"fontfile='{escape_drawtext(font_file)}':"
        f"text='{escaped}':"
        f"fontsize={size}:"
        f"fontcolor={color}:"
        f"x={x}:y={y}:"
        f"line_spacing={line_spacing}:"
        "shadowcolor=black@0.55:shadowx=3:shadowy=3"
    )


def scale_font(size: int, width: int) -> int:
    return max(18, int(size * width / 720))


def escape_drawtext(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("\n", "\\n")
    )


def wrap_text(value: str, width: int) -> str:
    words = str(value or "").split()
    if not words:
        return ""

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
    return "\n".join(lines[:5])
