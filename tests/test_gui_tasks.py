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


def make_media(
    title: str,
    *,
    video: bool = True,
    audio: bool = True,
    subtitle_language: str | None = None,
) -> MediaInfo:
    formats: list[MediaFormat] = []
    if video:
        formats.append(MediaFormat("video-1", "video", "mp4"))
    if audio:
        formats.append(MediaFormat("audio-1", "audio", "m4a"))
    subtitles = (
        [SubtitleInfo(subtitle_language, subtitle_language, "https://example.com/subtitle.vtt", "vtt", "ytdlp")]
        if subtitle_language
        else []
    )
    return MediaInfo(
        title=title,
        source_url=f"https://example.com/{title}",
        source_type="ytdlp",
        formats=formats,
        subtitles=subtitles,
    )


def parsed_task(task_id: int, media: MediaInfo, selection: TaskSelection) -> BatchTask:
    return BatchTask(
        task_id=task_id,
        url=media.source_url,
        media=media,
        status="parsed",
        parse_status="parsed",
        selection=selection,
    )


def test_parse_tasks_keeps_invalid_row_and_deduplicates_valid_urls() -> None:
    tasks = parse_tasks(
        "\nhttps://example.com/one\nnot-a-url\nhttps://example.com/one\nhttps://example.com/two\n"
    )

    assert [task.task_id for task in tasks] == [1, 2, 3]
    assert [task.url for task in tasks] == [
        "https://example.com/one",
        "not-a-url",
        "https://example.com/two",
    ]
    assert [task.source_line for task in tasks] == [2, 3, 5]
    assert tasks[0].parse_status == "pending"
    assert tasks[1].status == "parse_failed"
    assert tasks[1].selection.include is False
    assert "第 3 行网址无效" in (tasks[1].parse_error or "")


def test_analyse_tasks_sets_independent_defaults_and_continues_after_failure(tmp_path: Path) -> None:
    tasks = parse_tasks("https://example.com/video\nhttps://example.com/fails\nnot-a-url")
    media = make_media("video")
    updates: list[tuple[int, str, float | None]] = []

    def extract(url: str, **_kwargs) -> MediaInfo:
        if url.endswith("fails"):
            raise RuntimeError("无法解析第二个网址")
        return media

    config = AppConfig(output_dir=tmp_path, browser_cookies="chrome")
    with patch("downloader.batch.extract_media", side_effect=extract) as mocked_extract:
        returned = analyse_tasks(
            tasks,
            config,
            logging.getLogger("test_gui_tasks_analysis"),
            lambda task, message, percent: updates.append((task.task_id, message, percent)),
        )

    assert returned is tasks
    assert [call.args[0] for call in mocked_extract.call_args_list] == [
        "https://example.com/video",
        "https://example.com/fails",
    ]
    assert all(call.kwargs["browser_cookies"] == "chrome" for call in mocked_extract.call_args_list)
    assert tasks[0].status == "parsed"
    assert tasks[0].selection == TaskSelection(include=True, video=True)
    assert tasks[1].status == "parse_failed"
    assert tasks[1].selection.include is False
    assert tasks[1].parse_error == "无法解析第二个网址"
    assert tasks[2].status == "parse_failed"
    assert {task_id for task_id, _message, _percent in updates} == {1, 2, 3}


def test_download_plan_keeps_each_task_selection_in_a_detached_snapshot() -> None:
    chinese = make_media("chinese", subtitle_language="zh-CN")
    english = make_media("english", subtitle_language="en")
    first = parsed_task(1, chinese, TaskSelection(include=True, video=True, subtitle_language="zh-CN"))
    excluded = parsed_task(2, chinese, TaskSelection(include=False, audio=True))
    third = parsed_task(3, english, TaskSelection(include=True, audio=True, subtitle_language="en", transcript=True))

    plan = build_download_plan([first, excluded, third])
    first.selection.video = False
    first.selection.subtitle_language = None
    third.selection.subtitle_language = None
    third.selection.transcript_model = "small"

    assert [task.task_id for task in plan] == [1, 3]
    assert [task.status for task in plan] == ["queued", "queued"]
    assert plan[0] is not first
    assert plan[0].selection == TaskSelection(include=True, video=True, subtitle_language="zh-CN")
    assert plan[1].selection == TaskSelection(
        include=True,
        audio=True,
        subtitle_language="en",
        transcript=True,
        transcript_model="base",
    )


def test_download_plan_reports_every_included_task_with_invalid_choices() -> None:
    audio_only = make_media("audio-only", video=False, audio=True)
    no_content = parsed_task(1, audio_only, TaskSelection(include=True))
    missing_video = parsed_task(2, audio_only, TaskSelection(include=True, video=True))

    with pytest.raises(BatchValidationError) as caught:
        build_download_plan([no_content, missing_video])

    assert caught.value.task_errors == {
        1: ["请至少选择一种要下载的内容。"],
        2: ["当前页面没有可下载的视频。"],
    }


def test_execute_tasks_keeps_later_content_and_tasks_running_after_failure(tmp_path: Path) -> None:
    first_media = make_media("first")
    second_media = make_media("second", video=False, audio=True)
    first = parsed_task(1, first_media, TaskSelection(include=True, video=True, audio=True))
    second = parsed_task(2, second_media, TaskSelection(include=True, audio=True))
    order: list[str] = []
    updates: list[tuple[int, str, float | None]] = []

    def download_video(_self, media: MediaInfo) -> DownloadResult:
        order.append(f"{media.title}:video")
        raise RuntimeError("视频下载失败")

    def download_audio(_self, media: MediaInfo) -> DownloadResult:
        order.append(f"{media.title}:audio")
        return DownloadResult(tmp_path / "Audio" / f"{media.title}.m4a", "audio")

    with (
        patch("downloader.batch.MediaDownloader.download_video", autospec=True, side_effect=download_video),
        patch("downloader.batch.MediaDownloader.download_audio", autospec=True, side_effect=download_audio),
    ):
        executed = execute_tasks(
            [first, second],
            AppConfig(output_dir=tmp_path),
            logging.getLogger("test_gui_tasks_execution"),
            lambda task, message, percent: updates.append((task.task_id, message, percent)),
        )

    assert executed == [first, second]
    assert order == ["first:video", "first:audio", "second:audio"]
    assert first.download_status == "partial"
    assert [result.path.name for result in first.results] == ["first.m4a"]
    assert first.download_errors == ["视频：视频下载失败"]
    assert second.download_status == "completed"
    assert [result.path.name for result in second.results] == ["second.m4a"]
    assert any(task_id == 1 and "视频下载失败" in message for task_id, message, _percent in updates)
    assert updates[-1] == (2, "[2/2] 下载完成", 100.0)
