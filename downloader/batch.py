"""In-memory task management for sequential batch media downloads.

This module deliberately has no Tkinter dependency.  A caller can keep the
``BatchTask`` objects in its UI state, run analysis or downloads in a worker
thread, and use the callback to marshal the changed task back to the UI
thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Literal

from config import AppConfig

from .core import MediaDownloader
from .extractor import extract_media, validate_url
from .models import DownloadResult, MediaInfo
from .subtitle import choose_subtitle, download_subtitle
from .transcriber import WHISPER_MODELS, transcribe_media


TaskStatus = Literal[
    "pending",
    "parsing",
    "parsed",
    "parse_failed",
    "queued",
    "downloading",
    "completed",
    "partial",
    "failed",
    "skipped",
]
BatchProgressCallback = Callable[["BatchTask", str, float | None], None]


@dataclass(slots=True)
class TaskSelection:
    """The download choices belonging to one parsed URL."""

    include: bool = True
    video: bool = False
    audio: bool = False
    subtitle_language: str | None = None
    transcript: bool = False
    transcript_model: str = "base"

    def snapshot(self) -> "TaskSelection":
        """Return a detached, normalized copy suitable for a worker thread."""
        language = (self.subtitle_language or "").strip() or None
        return replace(
            self,
            subtitle_language=language,
            transcript_model=(self.transcript_model or "").strip(),
        )

    @property
    def has_selected_content(self) -> bool:
        return self.video or self.audio or bool(self.subtitle_language and self.subtitle_language.strip()) or self.transcript


@dataclass(slots=True)
class BatchTask:
    """A stable, independent batch item and its current lifecycle state."""

    task_id: int
    url: str
    media: MediaInfo | None = None
    status: TaskStatus = "pending"
    parse_status: TaskStatus = "pending"
    download_status: TaskStatus = "pending"
    selection: TaskSelection = field(default_factory=TaskSelection)
    source_line: int | None = None
    parse_error: str | None = None
    download_errors: list[str] = field(default_factory=list)
    results: list[DownloadResult] = field(default_factory=list)
    progress: float | None = None
    progress_message: str = ""

    def __post_init__(self) -> None:
        """Treat a directly supplied media object as an already parsed task."""
        if self.media is not None and self.parse_status == "pending":
            self.parse_status = "parsed"
            if self.status == "pending":
                self.status = "parsed"

    @property
    def title(self) -> str:
        return self.media.title if self.media else self.url

    @property
    def label(self) -> str:
        return f"第 {self.task_id} 个（{self.title}）"

    @property
    def errors(self) -> list[str]:
        """All known errors in display order, without exposing mutable internals."""
        return ([self.parse_error] if self.parse_error else []) + list(self.download_errors)

    @property
    def error(self) -> str | None:
        """The most recent error, for compact task-list displays."""
        if self.download_errors:
            return self.download_errors[-1]
        return self.parse_error


class BatchValidationError(ValueError):
    """Raised before a batch starts when one or more selected tasks are invalid."""

    def __init__(self, task_errors: dict[int, list[str]], tasks: Iterable[BatchTask]) -> None:
        self.task_errors = {task_id: list(errors) for task_id, errors in task_errors.items()}
        labels = {task.task_id: task.label for task in tasks}
        lines = [
            f"{labels.get(task_id, f'第 {task_id} 个任务')}：{error}"
            for task_id, errors in self.task_errors.items()
            for error in errors
        ]
        super().__init__("\n".join(lines))


def parse_url_list(value: str) -> list[str]:
    """Strictly validate a multi-line URL field and retain first occurrences.

    ``parse_tasks`` is the UI-facing variant: it keeps malformed rows as
    failed tasks so a user can see them alongside successful rows.
    """
    lines = [(line_number, line.strip()) for line_number, line in enumerate(value.splitlines(), 1) if line.strip()]
    if not lines:
        raise ValueError("请至少输入一个公开网页 URL，每行一个。")

    urls: list[str] = []
    seen: set[str] = set()
    for line_number, candidate in lines:
        try:
            url = validate_url(candidate)
        except ValueError as exc:
            raise ValueError(f"第 {line_number} 行网址无效：{candidate}\n{exc}") from exc
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def parse_tasks(value: str) -> list[BatchTask]:
    """Create one task per first valid URL plus visible rows for malformed input.

    Blank lines are ignored.  Valid duplicate URLs are removed while keeping
    their first position.  Malformed URLs are retained as non-included failed
    tasks, allowing the rest of the batch to be parsed and downloaded.
    """
    lines = [(line_number, line.strip()) for line_number, line in enumerate(value.splitlines(), 1) if line.strip()]
    if not lines:
        raise ValueError("请至少输入一个公开网页 URL，每行一个。")

    tasks: list[BatchTask] = []
    seen: set[str] = set()
    for line_number, candidate in lines:
        try:
            url = validate_url(candidate)
        except ValueError as exc:
            tasks.append(BatchTask(
                task_id=len(tasks) + 1,
                url=candidate,
                source_line=line_number,
                status="parse_failed",
                parse_status="parse_failed",
                selection=TaskSelection(include=False),
                parse_error=f"第 {line_number} 行网址无效：{candidate}\n{exc}",
            ))
            continue
        if url in seen:
            continue
        seen.add(url)
        tasks.append(BatchTask(
            task_id=len(tasks) + 1,
            url=url,
            source_line=line_number,
            selection=TaskSelection(include=False),
        ))
    return tasks


def default_selection(media: MediaInfo) -> TaskSelection:
    """Default a parsed task to video, falling back to audio when needed."""
    has_video = bool(media.video_formats)
    return TaskSelection(
        include=True,
        video=has_video,
        audio=not has_video and bool(media.audio_formats),
    )


def subtitle_languages(media: MediaInfo | None) -> list[str]:
    """Return the task's available subtitle languages in source order."""
    if media is None:
        return []
    return list(dict.fromkeys(item.language for item in media.subtitles))


def analyse_tasks(
    tasks: Iterable[BatchTask],
    config: AppConfig,
    logger: logging.Logger | None = None,
    progress_callback: BatchProgressCallback | None = None,
) -> list[BatchTask]:
    """Sequentially extract media for every valid task, preserving failures.

    This function is safe to call from a worker thread.  It does not touch UI
    widgets; callers should forward ``progress_callback`` events to their UI
    message queue.
    """
    task_list = tasks if isinstance(tasks, list) else list(tasks)
    active_logger = logger or logging.getLogger(__name__)
    total = len(task_list)

    for index, task in enumerate(task_list, 1):
        if task.parse_status == "parse_failed" and task.media is None:
            _notify(task, f"[{index}/{total}] 网址格式无效", None, progress_callback)
            continue

        task.media = None
        task.parse_error = None
        task.download_errors.clear()
        task.results.clear()
        task.progress = 0.0
        task.progress_message = f"[{index}/{total}] 正在解析…"
        task.status = "parsing"
        task.parse_status = "parsing"
        task.download_status = "pending"
        _notify(task, task.progress_message, 0.0, progress_callback)

        try:
            media = extract_media(
                task.url,
                timeout=config.request_timeout,
                logger=active_logger,
                browser_cookies=config.browser_cookies,
            )
        except Exception as exc:  # One page must not prevent later URLs from being analysed.
            active_logger.exception("媒体解析失败：%s", task.url)
            detail = str(exc).strip() or f"解析组件发生内部错误：{type(exc).__name__}"
            task.parse_error = detail
            task.status = "parse_failed"
            task.parse_status = "parse_failed"
            task.selection = TaskSelection(include=False)
            _notify(task, f"[{index}/{total}] 解析失败：{detail}", None, progress_callback)
        else:
            task.media = media
            task.selection = default_selection(media)
            task.status = "parsed"
            task.parse_status = "parsed"
            task.progress = 100.0
            _notify(task, f"[{index}/{total}] 解析完成", 100.0, progress_callback)
    return task_list


# American spelling is a harmless convenience for integrations outside the UI.
analyze_tasks = analyse_tasks


def task_validation_errors(task: BatchTask) -> list[str]:
    """Return actionable validation errors for one included task."""
    if not task.selection.include:
        return []
    if task.media is None or task.parse_status != "parsed":
        return ["尚未成功解析，无法下载。"]

    media = task.media
    selection = task.selection.snapshot()
    errors: list[str] = []
    if selection.video and not media.video_formats:
        errors.append("当前页面没有可下载的视频。")
    if selection.audio and not media.audio_formats:
        errors.append("当前页面没有可下载的音频。")
    if selection.subtitle_language and not any(
        item.language.casefold() == selection.subtitle_language.casefold() for item in media.subtitles
    ):
        errors.append(f"当前页面没有 {selection.subtitle_language} 字幕。")
    if selection.transcript:
        if not media.audio_formats:
            errors.append("当前页面没有可用于 Whisper 识别的音频。")
        elif selection.transcript_model not in WHISPER_MODELS:
            errors.append("请选择有效的 Whisper 模型。")
    if not selection.has_selected_content:
        errors.append("请至少选择一种要下载的内容。")
    return errors


def build_download_plan(tasks: Iterable[BatchTask]) -> list[BatchTask]:
    """Validate selected tasks and return detached selection snapshots.

    The returned objects are intentionally separate from UI-owned task state.
    A worker can therefore run safely while the UI renders or refreshes its
    original list.  Progress events carry the stable ``task_id`` for mapping
    worker state back to UI state.
    """
    task_list = tasks if isinstance(tasks, list) else list(tasks)
    selected = [task for task in task_list if task.selection.include]
    if not selected:
        raise ValueError("请至少勾选一个参与下载的任务。")

    errors = {task.task_id: task_errors for task in selected if (task_errors := task_validation_errors(task))}
    if errors:
        raise BatchValidationError(errors, task_list)

    return [_snapshot_task(task) for task in selected]


def execute_tasks(
    tasks: Iterable[BatchTask],
    config: AppConfig,
    logger: logging.Logger | None = None,
    progress_callback: BatchProgressCallback | None = None,
) -> list[BatchTask]:
    """Run a validated plan sequentially while isolating task and job failures.

    Call :func:`build_download_plan` before this function to validate choices
    and capture the selection snapshot.  The function also guards malformed
    direct calls so an invalid task becomes failed without stopping later ones.
    """
    task_list = tasks if isinstance(tasks, list) else list(tasks)
    active_logger = logger or logging.getLogger(__name__)
    total = len(task_list)

    for index, task in enumerate(task_list, 1):
        _execute_task(task, index, total, config, active_logger, progress_callback)
    return task_list


def _snapshot_task(task: BatchTask) -> BatchTask:
    return BatchTask(
        task_id=task.task_id,
        url=task.url,
        media=task.media,
        status="queued",
        parse_status=task.parse_status,
        download_status="queued",
        selection=task.selection.snapshot(),
        source_line=task.source_line,
    )


def _execute_task(
    task: BatchTask,
    index: int,
    total: int,
    config: AppConfig,
    logger: logging.Logger,
    progress_callback: BatchProgressCallback | None,
) -> None:
    if not task.selection.include:
        task.status = "skipped"
        task.download_status = "skipped"
        _notify(task, f"[{index}/{total}] 已跳过", None, progress_callback)
        return

    validation_errors = task_validation_errors(task)
    if validation_errors:
        task.download_errors = validation_errors
        task.status = "failed"
        task.download_status = "failed"
        _notify(task, f"[{index}/{total}] 下载前校验失败：{validation_errors[0]}", None, progress_callback)
        return

    assert task.media is not None  # Narrowed by task_validation_errors above.
    task.results.clear()
    task.download_errors.clear()
    task.status = "downloading"
    task.download_status = "downloading"
    task.progress = 0.0
    _notify(task, f"[{index}/{total}] 正在下载…", 0.0, progress_callback)

    def task_progress(message: str, percent: float | None) -> None:
        _notify(task, f"[{index}/{total}] {message}", percent, progress_callback)

    downloader = MediaDownloader(config, logger, task_progress)
    operations: list[tuple[str, Callable[[], DownloadResult]]] = []
    if task.selection.video:
        operations.append(("视频", lambda: downloader.download_video(task.media)))
    if task.selection.audio:
        operations.append(("音频", lambda: downloader.download_audio(task.media)))
    if task.selection.subtitle_language:
        language = task.selection.subtitle_language
        operations.append((
            "字幕",
            lambda: download_subtitle(
                task.media,
                choose_subtitle(task.media, language),
                config,
                task_progress,
            ),
        ))
    if task.selection.transcript:
        model = task.selection.transcript_model
        operations.append((
            "Whisper 字幕",
            lambda: transcribe_media(task.media, config, model, logger, task_progress),
        ))

    for label, operation in operations:
        task_progress(f"开始处理{label}…", 0.0)
        try:
            result = operation()
        except Exception as exc:  # Preserve later selected operations and later tasks.
            logger.exception("%s下载失败：%s", label, task.url)
            task.download_errors.append(f"{label}：{exc}")
            task_progress(f"{label}下载失败：{exc}", None)
        else:
            task.results.append(result)
            logger.info("%s下载成功：%s", label, result.path)

    if task.download_errors and task.results:
        task.status = "partial"
        task.download_status = "partial"
        task.progress = 100.0
        _notify(task, f"[{index}/{total}] 部分内容下载完成", 100.0, progress_callback)
    elif task.download_errors:
        task.status = "failed"
        task.download_status = "failed"
        _notify(task, f"[{index}/{total}] 下载失败", None, progress_callback)
    else:
        task.status = "completed"
        task.download_status = "completed"
        task.progress = 100.0
        _notify(task, f"[{index}/{total}] 下载完成", 100.0, progress_callback)


def _notify(
    task: BatchTask,
    message: str,
    percent: float | None,
    progress_callback: BatchProgressCallback | None,
) -> None:
    task.progress_message = message
    task.progress = percent
    if progress_callback:
        progress_callback(task, message, percent)


__all__ = [
    "BatchProgressCallback",
    "BatchTask",
    "BatchValidationError",
    "TaskSelection",
    "TaskStatus",
    "analyse_tasks",
    "analyze_tasks",
    "build_download_plan",
    "default_selection",
    "execute_tasks",
    "parse_tasks",
    "parse_url_list",
    "subtitle_languages",
    "task_validation_errors",
]
