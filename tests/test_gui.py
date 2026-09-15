import logging
import queue
from pathlib import Path
from unittest.mock import patch

import pytest

from config import AppConfig
from downloader.batch import BatchTask, TaskSelection, parse_url_list
from downloader.models import DownloadResult, MediaFormat, MediaInfo, SubtitleInfo
from gui import (
    MediaDownloaderApp,
    default_download_selection,
    media_table_rows,
    subtitle_languages,
    task_selection_label,
    task_status_label,
    task_title,
)


def sample_media() -> MediaInfo:
    return MediaInfo(
        title="示例",
        source_url="https://example.com/watch",
        source_type="ytdlp",
        formats=[
            MediaFormat("137", "video", "mp4", width=1920, height=1080, video_codec="avc1", filesize=1024),
            MediaFormat("140", "audio", "m4a", audio_codec="mp4a", filesize=2048),
        ],
        subtitles=[
            SubtitleInfo("zh-CN", "中文", "https://example.com/zh.vtt", "vtt", "ytdlp"),
            SubtitleInfo("en", "English", "https://example.com/en.vtt", "vtt", "ytdlp"),
        ],
    )


def parsed_task(task_id: int, media: MediaInfo, selection: TaskSelection | None = None) -> BatchTask:
    return BatchTask(
        task_id=task_id,
        url=media.source_url,
        media=media,
        status="parsed",
        parse_status="parsed",
        selection=selection or TaskSelection(include=True, video=True),
    )


def test_media_information_maps_to_detail_table_rows() -> None:
    media = sample_media()

    assert media_table_rows(media) == [
        ("视频", "137 / mp4", "avc1", "1920×1080 / 1.0 KB"),
        ("音频", "140 / m4a", "mp4a", "2.0 KB"),
        ("字幕", "zh-CN / vtt", "中文", "ytdlp"),
        ("字幕", "en / vtt", "English", "ytdlp"),
    ]
    assert subtitle_languages(media) == ["zh-CN", "en"]


def test_strict_url_list_keeps_first_valid_occurrence_and_reports_bad_lines() -> None:
    assert parse_url_list(" https://example.com/one\n\nhttps://example.com/two\nhttps://example.com/one ") == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    with pytest.raises(ValueError, match="第 2 行网址无效"):
        parse_url_list("https://example.com/one\nnot-a-url")


def test_default_selection_prefers_video_then_audio() -> None:
    assert default_download_selection(sample_media()) == (True, False)
    audio_only = MediaInfo(
        title="audio",
        source_url="https://example.com/audio",
        source_type="html",
        formats=[MediaFormat("html-1", "audio", "m4a")],
    )
    assert default_download_selection(audio_only) == (False, True)


def test_task_labels_and_row_values_reflect_one_task_selection() -> None:
    selection = TaskSelection(include=True, video=True, audio=True, subtitle_language="zh-CN", transcript=True)
    task = parsed_task(7, sample_media(), selection)
    failed = parsed_task(8, sample_media())
    failed.status = "failed"
    app = object.__new__(MediaDownloaderApp)

    assert task_title(task) == "示例"
    assert task_selection_label(selection) == "视频、音频、字幕 zh-CN、Whisper base"
    assert task_status_label(task) == "解析完成"
    assert task_status_label(failed) == "下载失败"
    assert app._task_row_values(task) == ("☑", "示例", "解析完成", "视频、音频、字幕 zh-CN、Whisper base", "")


def test_merge_task_replaces_the_matching_stable_task_id_only() -> None:
    app = object.__new__(MediaDownloaderApp)
    original = parsed_task(1, sample_media())
    other = parsed_task(2, sample_media())
    incoming = parsed_task(1, sample_media())
    incoming.status = "downloading"
    app.tasks = [original, other]
    app.current_task_id = 1
    updated: list[BatchTask] = []
    loaded: list[BatchTask | None] = []
    app._update_task_row = updated.append
    app._load_task_detail = loaded.append

    app._merge_task(incoming)

    assert app.tasks == [incoming, other]
    assert updated == [incoming]
    assert loaded == [incoming]


def test_merge_completed_tasks_preserves_unselected_and_parse_failed_rows() -> None:
    app = object.__new__(MediaDownloaderApp)
    completed = parsed_task(1, sample_media())
    completed.status = "completed"
    excluded = parsed_task(2, sample_media(), TaskSelection(include=False, audio=True))
    invalid = BatchTask(
        task_id=3,
        url="not-a-url",
        status="parse_failed",
        parse_status="parse_failed",
        selection=TaskSelection(include=False),
        parse_error="网址无效",
    )
    app.tasks = [parsed_task(1, sample_media()), excluded, invalid]

    app._merge_completed_tasks([completed])

    assert app.tasks == [completed, excluded, invalid]


def test_download_summary_accepts_a_transcript_result_kind() -> None:
    app = object.__new__(MediaDownloaderApp)
    completed = parsed_task(1, sample_media())
    completed.status = "completed"
    completed.results = [DownloadResult(Path("D:/MediaDownloader/Subtitle/example.whisper.vtt"), "transcript")]
    app.tasks = [completed]
    progress_updates: list[tuple[str, float | None]] = []
    windows: list[tuple[str, str]] = []
    app._set_progress = lambda message, percent: progress_updates.append((message, percent))
    app._show_result_window = lambda title, text: windows.append((title, text))

    app._show_download_summary()

    assert progress_updates == [("下载完成：成功 1 项，失败 0 项。", 100.0)]
    assert windows == [("批量下载结果", "1. 示例 — 已完成\n  已保存 Whisper 字幕：D:\\MediaDownloader\\Subtitle\\example.whisper.vtt")]


def test_analysis_worker_only_posts_backend_completion_event(tmp_path) -> None:
    app = object.__new__(MediaDownloaderApp)
    app.events = queue.Queue()
    tasks = [parsed_task(1, sample_media())]
    config = AppConfig(output_dir=tmp_path)

    with patch("gui.analyse_tasks", return_value=tasks) as analyse:
        app._analysis_worker(tasks, config, logging.getLogger("test_gui_analysis_worker"))

    assert app.events.get_nowait() == ("analysis_complete", tasks)
    callback = analyse.call_args.args[3]
    assert getattr(callback, "__self__", None) is app


def test_download_worker_only_posts_backend_completion_event(tmp_path) -> None:
    app = object.__new__(MediaDownloaderApp)
    app.events = queue.Queue()
    plan = [parsed_task(1, sample_media())]
    config = AppConfig(output_dir=tmp_path)

    with patch("gui.execute_tasks", return_value=plan) as execute:
        app._download_worker(plan, config, logging.getLogger("test_gui_download_worker"))

    assert app.events.get_nowait() == ("download_complete", plan)
    callback = execute.call_args.args[3]
    assert getattr(callback, "__self__", None) is app
