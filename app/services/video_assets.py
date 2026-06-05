from __future__ import annotations

import asyncio
import base64
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
    audio_duration = await asyncio.to_thread(probe_audio_duration, audio_path, ffmpeg_path)
    duration = max(
        int(settings.video_assets_duration_seconds or 30),
        math.ceil(audio_duration + 0.8) if audio_duration else 0,
    )
    duration = max(18, min(90, duration))

    title = match_title(prediction, pick)
    main_bet = bet_name(pick, "ru") if pick else simple_bet_name(prediction.main_bet_label or prediction.main_bet_code, "ru")
    confidence = str(pick.confidence if pick else prediction.confidence)
    why = compact_text((pick.why_this_match_is_gold if pick else "") or prediction.rendered_text, 150)
    time_text = format_start_time(prediction.start_time)
    odds = prediction.bookmaker_odds or (f"{pick.bookmaker_odds:.2f}" if pick and pick.bookmaker_odds else "")
    bookmaker = prediction.bookmaker_name or (pick.bookmaker_name if pick else "") or settings.bookmaker_name

    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    local_font = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    font_file = local_font if Path(local_font).exists() else font

    pad = width // 12
    title_y = height // 7
    pick_y = height // 3
    filters = [
        f"color=c=0x08111f:s={width}x{height}:r={fps}:d={duration}",
        "format=yuv420p",
        f"drawbox=x=0:y=0:w={width}:h={height}:color=0x020712@0.40:t=fill",
        f"drawbox=x=0:y=0:w={width}:h={height // 4}:color=0x063559@0.32:t=fill",
        f"drawbox=x=0:y={height - height // 5}:w={width}:h={height // 5}:color=0x063a24@0.30:t=fill",
        f"drawbox=x='mod(t*85,{width + 180})-180':y={height // 13}:w=180:h=3:color=0x32d583@0.92:t=fill",
        f"drawbox=x='mod(t*55,{width + 260})-260':y={height - height // 6}:w=260:h=3:color=0x8ab4ff@0.65:t=fill",
        f"drawbox=x={pad - 16}:y={title_y - 28}:w={width - 2 * pad + 32}:h={height // 4}:color=black@0.25:t=fill:enable='between(t,0.2,{duration})'",
        f"drawbox=x={pad - 16}:y={pick_y - 30}:w={width - 2 * pad + 32}:h={height // 4}:color=black@0.22:t=fill:enable='between(t,2.6,{duration})'",
        f"drawbox=x={pad}:y={height // 15}:w={width - 2 * pad}:h=4:color=0x32d583@0.95:t=fill",
        draw_text(font_file, "TELONYX AI SIGNAL", scale_font(30, width), pad, height // 23, "0x32d583", alpha="if(lt(t,0.4),t/0.4,1)"),
        "fade=t=in:st=0:d=0.45",
    ]
    filters.extend(draw_text_block(font_file, title.upper(), scale_font(50, width), pad, title_y, "white", 18, 3, line_gap=12, start=0.35))
    filters.extend(draw_text_block(font_file, "MAIN PICK", scale_font(25, width), pad, pick_y, "0x8ab4ff", 28, 1, start=2.5))
    filters.extend(draw_text_block(font_file, main_bet.upper(), scale_font(43, width), pad, pick_y + scale_font(46, width), "white", 20, 2, line_gap=10, start=2.9))
    filters.extend(draw_text_block(font_file, f"CONFIDENCE {confidence}/100", scale_font(36, width), pad, height // 2 + 20, "0x32d583", 26, 1, start=5.2))
    filters.extend(draw_text_block(font_file, f"{bookmaker} {odds}".strip(), scale_font(28, width), pad, height // 2 + 82, "0xfed766", 26, 1, start=5.6))
    filters.extend(draw_text_block(font_file, why, scale_font(29, width), pad, height // 2 + 175, "white", 29, 4, line_gap=9, start=7.0))
    filters.extend(draw_text_block(font_file, time_text, scale_font(24, width), pad, height - 205, "0xb8c4d9", 28, 1, start=10.0))
    filters.extend(draw_text_block(font_file, "FULL BREAKDOWN IN TELEGRAM", scale_font(28, width), pad, height - 132, "0x32d583", 29, 1, start=11.0))
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
