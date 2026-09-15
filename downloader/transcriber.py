"""Generate VTT subtitles from media audio with faster-whisper."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from config import AppConfig
from utils.filename import unique_path

from .core import MediaDownloader
from .models import DownloadResult, MediaInfo, ProgressCallback

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore[assignment,misc]

try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None  # type: ignore[assignment,misc]


WHISPER_MODELS = ("tiny", "base", "small")
_SIMPLIFIED_CHINESE_CONVERTER = None


class WhisperTranscriptionError(RuntimeError):
    """Raised when audio cannot be transcribed into a VTT file."""


def transcribe_media(
    media: MediaInfo,
    config: AppConfig,
    model_name: str = "base",
    logger: logging.Logger | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Download temporary audio, transcribe it, then remove the temporary audio."""
    if not media.audio_formats:
        raise WhisperTranscriptionError("当前页面没有可用于语音识别的音频。")
    if model_name not in WHISPER_MODELS:
        raise WhisperTranscriptionError(f"不支持的 Whisper 模型：{model_name}")

    config.ensure_directories()
    task_dir = config.temp_dir / f"whisper-{uuid.uuid4().hex}"
    temporary_config = AppConfig(
        output_dir=task_dir,
        request_timeout=config.request_timeout,
        max_filename_length=config.max_filename_length,
        ffmpeg_path=config.resolve_ffmpeg(),
        keep_failed_temp_files=config.keep_failed_temp_files,
        browser_cookies=config.browser_cookies,
    )
    succeeded = False

    def audio_progress(message: str, percent: float | None) -> None:
        if progress_callback:
            mapped = percent * 0.3 if percent is not None else None
            progress_callback(message, mapped)

    try:
        audio = MediaDownloader(temporary_config, logger, audio_progress).download_audio(media)
        result = transcribe_audio(
            audio.path,
            media.title,
            config,
            model_name=model_name,
            progress_callback=progress_callback,
        )
        succeeded = True
        return result
    except WhisperTranscriptionError:
        raise
    except Exception as exc:
        raise WhisperTranscriptionError(f"Whisper 字幕生成失败：{exc}") from exc
    finally:
        if succeeded or not config.keep_failed_temp_files:
            shutil.rmtree(task_dir, ignore_errors=True)


def transcribe_audio(
    audio_path: Path,
    title: str,
    config: AppConfig,
    model_name: str = "base",
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Transcribe one local audio file and save a UTF-8 WebVTT subtitle."""
    config.ensure_directories()
    if WhisperModel is None:
        raise WhisperTranscriptionError("未安装 faster-whisper；请运行 pip install -r requirements.txt。")
    if model_name not in WHISPER_MODELS:
        raise WhisperTranscriptionError(f"不支持的 Whisper 模型：{model_name}")
    if not audio_path.is_file():
        raise WhisperTranscriptionError("用于语音识别的临时音频不存在。")

    model_dir = config.output_dir / "WhisperModels"
    model_dir.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        progress_callback(f"正在加载 Whisper {model_name} 模型（首次使用会下载）…", None)

    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(model_dir))
        segments, info = model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
        language = getattr(info, "language", None) or "und"
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        temporary_vtt = audio_path.parent / "whisper.vtt"
        cue_count = _write_vtt(
            segments,
            temporary_vtt,
            duration,
            progress_callback,
            simplify_chinese=language.lower().startswith("zh"),
        )
    except WhisperTranscriptionError:
        raise
    except Exception as exc:
        raise WhisperTranscriptionError(f"Whisper 识别失败：{exc}") from exc

    if cue_count == 0:
        raise WhisperTranscriptionError("Whisper 没有识别到可生成字幕的语音。")
    destination = unique_path(
        config.subtitle_dir,
        f"{title}.whisper.{language}",
        ".vtt",
        config.max_filename_length,
    )
    shutil.move(str(temporary_vtt), str(destination))
    if progress_callback:
        progress_callback("Whisper 字幕生成完成", 100.0)
    return DownloadResult(path=destination, kind="subtitle", merged_or_converted=True)


def _write_vtt(
    segments,
    destination: Path,
    duration: float,
    progress_callback: ProgressCallback | None,
    simplify_chinese: bool = False,
) -> int:
    cue_count = 0
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        output.write("WEBVTT\n\n")
        for segment in segments:
            text = " ".join(str(segment.text).split())
            if simplify_chinese:
                text = to_simplified_chinese(text)
            if not text:
                continue
            cue_count += 1
            start = max(0.0, float(segment.start))
            end = max(start + 0.001, float(segment.end))
            output.write(f"{cue_count}\n{format_vtt_timestamp(start)} --> {format_vtt_timestamp(end)}\n{text}\n\n")
            if progress_callback:
                percent = 30.0 + min(65.0, end * 65.0 / duration) if duration > 0 else None
                progress_callback("正在使用 Whisper 识别语音…", percent)
    return cue_count


def to_simplified_chinese(text: str) -> str:
    """Convert Traditional Chinese text to Simplified Chinese with OpenCC."""
    if OpenCC is None:
        raise WhisperTranscriptionError("未安装中文转换组件；请运行 pip install -r requirements.txt。")

    global _SIMPLIFIED_CHINESE_CONVERTER
    if _SIMPLIFIED_CHINESE_CONVERTER is None:
        _SIMPLIFIED_CHINESE_CONVERTER = OpenCC("t2s")
    return _SIMPLIFIED_CHINESE_CONVERTER.convert(text)


def format_vtt_timestamp(seconds: float) -> str:
    milliseconds = round(max(0.0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
