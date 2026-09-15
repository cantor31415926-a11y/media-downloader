import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from config import AppConfig
from downloader.batch import (
    BatchTask,
    BatchValidationError,
    TaskSelection,
    analyse_tasks,
    build_download_plan,
    execute_tasks,
    parse_tasks,
)
from downloader.models import DownloadResult, MediaFormat, MediaInfo, SubtitleInfo


def sample_media(title: str = "示例") -> MediaInfo:
    return MediaInfo(
        title=title,
        source_url="https://example.com/watch",
        source_type="ytdlp",
        formats=[
            MediaFormat("137", "video", "mp4"),
            MediaFormat("140", "audio", "m4a"),
        ],
        subtitles=[SubtitleInfo("zh-CN", "中文", "https://example.com/zh.vtt", "vtt", "ytdlp")],
    )


def test_parse_tasks_deduplicates_valid_urls_and_keeps_invalid_row() -> None:
    tasks = parse_tasks(
        " https://example.com/one\n\nhttps://example.com/one\nnot-a-url\nhttps://example.com/two "
    )

    assert [(task.task_id, task.url, task.source_line) for task in tasks] == [
        (1, "https://example.com/one", 1),
        (2, "not-a-url", 4),
        (3, "https://example.com/two", 5),
    ]
    assert tasks[1].status == "parse_failed"
    assert tasks[1].selection.include is False
    assert "第 4 行网址无效" in (tasks[1].parse_error or "")


def test_analyse_tasks_continues_after_failure_and_sets_default_selection(tmp_path: Path) -> None:
    tasks = parse_tasks("https://example.com/one\nhttps://example.com/two")
    updates: list[tuple[int, str, float | None]] = []

    with patch("downloader.batch.extract_media", side_effect=[RuntimeError("解析故障"), sample_media()] ) as extract:
        result = analyse_tasks(
            tasks,
            AppConfig(output_dir=tmp_path, browser_cookies="chrome"),
            progress_callback=lambda task, message, percent: updates.append((task.task_id, message, percent)),
        )

    assert result is tasks
    assert extract.call_count == 2
    assert extract.call_args_list[0].kwargs["browser_cookies"] == "chrome"
    assert tasks[0].status == "parse_failed"
    assert tasks[0].parse_error == "解析故障"
    assert tasks[0].selection.include is False
    assert tasks[1].status == "parsed"
    assert tasks[1].selection == TaskSelection(include=True, video=True)
    assert updates[-1] == (2, "[2/2] 解析完成", 100.0)


def test_build_download_plan_validates_each_selected_task_and_snapshots_choices() -> None:
    parsed = BatchTask(
        1,
        "https://example.com/watch",
        media=sample_media(),
        status="parsed",
        parse_status="parsed",
        selection=TaskSelection(video=True, subtitle_language=" zh-CN "),
    )
    missing_selection = BatchTask(
        2,
        "https://example.com/other",
        media=sample_media("另一个"),
        status="parsed",
        parse_status="parsed",
        selection=TaskSelection(),
    )

    with pytest.raises(BatchValidationError, match="至少选择") as error:
        build_download_plan([parsed, missing_selection])
    assert error.value.task_errors == {2: ["请至少选择一种要下载的内容。"]}

    missing_selection.selection.include = False
    plan = build_download_plan([parsed, missing_selection])
    parsed.selection.video = False
    parsed.selection.subtitle_language = "en"

    assert plan[0] is not parsed
    assert plan[0].selection == TaskSelection(video=True, subtitle_language="zh-CN")
    assert plan[0].status == "queued"
    assert plan[0].results == []


def test_execute_tasks_keeps_job_and_task_failures_isolated(tmp_path: Path) -> None:
    first = BatchTask(
        1,
        "https://example.com/one",
        media=sample_media("第一个"),
        status="queued",
        parse_status="parsed",
        download_status="queued",
        selection=TaskSelection(video=True, audio=True, subtitle_language="zh-CN", transcript=True),
    )
    second = BatchTask(
        2,
        "https://example.com/two",
        media=sample_media("第二个"),
        status="queued",
        parse_status="parsed",
        download_status="queued",
        selection=TaskSelection(audio=True),
    )
    order: list[str] = []

    def fail_video(_self, _media):
        order.append("video")
        raise RuntimeError("视频故障")

    def download_audio(_self, media):
        order.append(f"audio:{media.title}")
        return DownloadResult(tmp_path / f"{media.title}.m4a", "audio")

    def subtitle(_media, _subtitle, _config, _callback):
        order.append("subtitle")
        return DownloadResult(tmp_path / "subtitle.vtt", "subtitle")

    def transcript(_media, _config, _model, _logger, _callback):
        order.append("transcript")
        return DownloadResult(tmp_path / "whisper.vtt", "subtitle")

    with (
        patch("downloader.batch.MediaDownloader.download_video", autospec=True, side_effect=fail_video),
        patch("downloader.batch.MediaDownloader.download_audio", autospec=True, side_effect=download_audio),
        patch("downloader.batch.choose_subtitle", return_value=sample_media().subtitles[0]),
        patch("downloader.batch.download_subtitle", side_effect=subtitle),
        patch("downloader.batch.transcribe_media", side_effect=transcript),
    ):
        returned = execute_tasks([first, second], AppConfig(output_dir=tmp_path), logging.getLogger("test_batch"))

    assert returned == [first, second]
    assert order == ["video", "audio:第一个", "subtitle", "transcript", "audio:第二个"]
    assert first.status == "partial"
    assert first.download_errors == ["视频：视频故障"]
    assert [result.kind for result in first.results] == ["audio", "subtitle", "subtitle"]
    assert second.status == "completed"
    assert [result.kind for result in second.results] == ["audio"]
