from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import AppConfig
from downloader.models import DownloadResult, MediaFormat, MediaInfo
from downloader.transcriber import format_vtt_timestamp, transcribe_audio, transcribe_media


def test_format_vtt_timestamp() -> None:
    assert format_vtt_timestamp(0) == "00:00:00.000"
    assert format_vtt_timestamp(3661.234) == "01:01:01.234"


def test_transcribe_audio_writes_utf8_vtt(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.m4a"
    audio_path.write_bytes(b"audio")

    class FakeModel:
        def __init__(self, model_name: str, **options) -> None:
            assert model_name == "base"
            assert options["device"] == "cpu"
            assert options["compute_type"] == "int8"

        def transcribe(self, path: str, **options):
            assert path == str(audio_path)
            assert options == {"beam_size": 5, "vad_filter": True}
            segments = iter([
                SimpleNamespace(start=0.0, end=1.25, text="  你好，歡迎使用語音識別  "),
                SimpleNamespace(start=1.25, end=2.5, text="第二句"),
            ])
            return segments, SimpleNamespace(language="zh", duration=2.5)

    updates: list[tuple[str, float | None]] = []
    with patch("downloader.transcriber.WhisperModel", FakeModel):
        result = transcribe_audio(
            audio_path,
            "示例",
            AppConfig(output_dir=tmp_path),
            progress_callback=lambda message, percent: updates.append((message, percent)),
        )

    assert result.path == tmp_path / "Subtitle" / "示例.whisper.zh.vtt"
    assert result.path.read_text(encoding="utf-8") == (
        "WEBVTT\n\n"
        "1\n00:00:00.000 --> 00:00:01.250\n你好，欢迎使用语音识别\n\n"
        "2\n00:00:01.250 --> 00:00:02.500\n第二句\n\n"
    )
    assert updates[-1] == ("Whisper 字幕生成完成", 100.0)


def test_transcribe_media_removes_temporary_audio_after_success(tmp_path: Path) -> None:
    media = MediaInfo(
        title="sample",
        source_url="https://example.com/watch",
        source_type="ytdlp",
        formats=[MediaFormat("140", "audio", "m4a")],
    )
    config = AppConfig(output_dir=tmp_path)

    def fake_download(downloader, _media):
        downloader.config.ensure_directories()
        audio = downloader.config.audio_dir / "temporary.m4a"
        audio.write_bytes(b"audio")
        return DownloadResult(audio, "audio")

    def fake_transcribe(audio_path, _title, final_config, **_options):
        assert "whisper-" in str(audio_path)
        destination = final_config.subtitle_dir / "sample.whisper.zh.vtt"
        destination.write_text("WEBVTT\n", encoding="utf-8")
        return DownloadResult(destination, "subtitle", True)

    with (
        patch("downloader.transcriber.MediaDownloader.download_audio", autospec=True, side_effect=fake_download),
        patch("downloader.transcriber.transcribe_audio", side_effect=fake_transcribe),
    ):
        result = transcribe_media(media, config)

    assert result.path.exists()
    assert list(config.temp_dir.glob("whisper-*")) == []
