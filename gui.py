"""Tkinter desktop interface for the public media downloader."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from config import AppConfig, DEFAULT_OUTPUT_DIR
from downloader.batch import (
    BatchTask,
    TaskSelection,
    analyse_tasks,
    build_download_plan,
    execute_tasks,
    parse_tasks,
)
from downloader.models import MediaInfo, format_filesize
from downloader.transcriber import WHISPER_MODELS
from utils.logger import configure_logger


TableRow = tuple[str, str, str, str]


def media_table_rows(media: MediaInfo) -> list[TableRow]:
    """Convert one parsed item into rows suitable for the detail table."""
    rows: list[TableRow] = []
    for kind_label, formats in (("视频", media.video_formats[:10]), ("音频", media.audio_formats[:10])):
        for item in formats:
            format_text = f"{item.format_id} / {item.extension or '未知'}"
            codecs = [codec for codec in (item.video_codec, item.audio_codec) if codec and codec != "none"]
            description = " / ".join(codecs) or item.description or "未知"
            dimensions = f"{item.width}×{item.height}" if item.width and item.height else ""
            size = format_filesize(item.filesize)
            details = " / ".join(part for part in (dimensions, size) if part) or "未知"
            rows.append((kind_label, format_text, description, details))
    for subtitle in media.subtitles:
        rows.append(("字幕", f"{subtitle.language} / {subtitle.extension or '未知'}", subtitle.name, subtitle.source))
    return rows


def subtitle_languages(media: MediaInfo) -> list[str]:
    """Return subtitle language codes in source order without duplicates."""
    return list(dict.fromkeys(item.language for item in media.subtitles))


def default_download_selection(media: MediaInfo) -> tuple[bool, bool]:
    """Prefer video, with audio as the default for audio-only pages."""
    has_video = bool(media.video_formats)
    return has_video, not has_video and bool(media.audio_formats)


def task_status_label(task: BatchTask) -> str:
    """Translate task states into concise labels for the task list."""
    labels = {
        "pending": "等待解析",
        "parsing": "正在解析",
        "parsed": "解析完成",
        "parse_failed": "解析失败",
        "queued": "等待下载",
        "downloading": "正在下载",
        "completed": "已完成",
        "partial": "部分完成",
        "failed": "下载失败",
        "skipped": "未参与",
    }
    return labels.get(task.status, task.status)


def task_selection_label(selection: TaskSelection) -> str:
    """Summarise the independent selection attached to one task."""
    parts: list[str] = []
    if selection.video:
        parts.append("视频")
    if selection.audio:
        parts.append("音频")
    if selection.subtitle_language:
        parts.append(f"字幕 {selection.subtitle_language}")
    if selection.transcript:
        parts.append(f"Whisper {selection.transcript_model}")
    return "、".join(parts) or "未选择"


def task_title(task: BatchTask) -> str:
    """Show a media title when available, otherwise the submitted URL."""
    return task.media.title if task.media else task.url


class MediaDownloaderApp:
    """A task-oriented desktop interface for sequential batch downloads."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("本地媒体下载器")
        self.root.geometry("1180x800")
        self.root.minsize(940, 680)

        self.tasks: list[BatchTask] = []
        self.current_task_id: int | None = None
        self.media_browser_cookies: str | None = None
        self.busy = False
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._indeterminate = False
        self._rendering_tasks = False
        self._loading_detail = False

        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.ffmpeg_var = tk.StringVar(value=AppConfig().resolve_ffmpeg() or "")
        self.chrome_cookies_var = tk.BooleanVar(value=False)
        self.detail_title_var = tk.StringVar(value="选择一个任务查看详情")
        self.detail_url_var = tk.StringVar(value="-")
        self.detail_status_var = tk.StringVar(value="-")
        self.detail_error_var = tk.StringVar(value="")
        self.participate_var = tk.BooleanVar(value=False)
        self.video_var = tk.BooleanVar(value=False)
        self.audio_var = tk.BooleanVar(value=False)
        self.subtitle_var = tk.BooleanVar(value=False)
        self.subtitle_language_var = tk.StringVar()
        self.transcript_var = tk.BooleanVar(value=False)
        self.whisper_model_var = tk.StringVar(value="base")
        self.status_var = tk.StringVar(value="请输入公开网页 URL（每行一个），然后点击“批量解析”。")

        self._build_ui()
        self._refresh_detail_controls()
        self.root.after(100, self._drain_events)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(4, weight=1)

        ttk.Label(container, text="网页 URL：").grid(row=0, column=0, sticky="nw", pady=(0, 8))
        url_frame = ttk.Frame(container)
        url_frame.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
        url_frame.columnconfigure(0, weight=1)
        self.url_text = tk.Text(url_frame, height=4, wrap="none")
        self.url_text.grid(row=0, column=0, sticky="ew")
        url_scrollbar = ttk.Scrollbar(url_frame, orient="vertical", command=self.url_text.yview)
        url_scrollbar.grid(row=0, column=1, sticky="ns")
        self.url_text.configure(yscrollcommand=url_scrollbar.set)
        ttk.Label(url_frame, text="每行粘贴一个网址；相同网址只保留首次输入；按 Ctrl+Enter 也可开始解析。", foreground="#666666").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(3, 0)
        )
        self.analyse_button = ttk.Button(container, text="批量解析", command=self._start_analysis)
        self.analyse_button.grid(row=0, column=2, sticky="new", pady=(0, 8))

        ttk.Label(container, text="保存到：").grid(row=1, column=0, sticky="w", pady=(0, 8))
        self.output_entry = ttk.Entry(container, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
        self.output_button = ttk.Button(container, text="浏览", command=self._choose_output_directory)
        self.output_button.grid(row=1, column=2, sticky="ew", pady=(0, 8))

        ttk.Label(container, text="FFmpeg：").grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.ffmpeg_entry = ttk.Entry(container, textvariable=self.ffmpeg_var)
        self.ffmpeg_entry.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
        self.ffmpeg_button = ttk.Button(container, text="选择", command=self._choose_ffmpeg)
        self.ffmpeg_button.grid(row=2, column=2, sticky="ew", pady=(0, 8))
        options_frame = ttk.Frame(container)
        options_frame.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(options_frame, text="留空时自动检查保存目录的 FFmpeg 文件夹和系统 PATH。", foreground="#666666").pack(side="left")
        self.chrome_cookies_check = ttk.Checkbutton(
            options_frame,
            text="使用 Chrome Cookie（仅当前运行）",
            variable=self.chrome_cookies_var,
        )
        self.chrome_cookies_check.pack(side="right")

        workspace = ttk.Panedwindow(container, orient="horizontal")
        workspace.grid(row=4, column=0, columnspan=3, sticky="nsew")

        task_frame = ttk.LabelFrame(workspace, text="下载任务", padding=10)
        task_frame.columnconfigure(0, weight=1)
        task_frame.rowconfigure(0, weight=1)
        workspace.add(task_frame, weight=1)

        task_columns = ("include", "title", "status", "selection", "progress")
        self.task_tree = ttk.Treeview(task_frame, columns=task_columns, show="headings", selectmode="browse")
        task_headings = {"include": "下载", "title": "标题 / 网址", "status": "状态", "selection": "已选内容", "progress": "进度"}
        task_widths = {"include": 48, "title": 235, "status": 75, "selection": 155, "progress": 56}
        for column in task_columns:
            self.task_tree.heading(column, text=task_headings[column])
            self.task_tree.column(column, width=task_widths[column], minwidth=45, stretch=column == "title")
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        task_scrollbar = ttk.Scrollbar(task_frame, orient="vertical", command=self.task_tree.yview)
        task_scrollbar.grid(row=0, column=1, sticky="ns")
        self.task_tree.configure(yscrollcommand=task_scrollbar.set)
        ttk.Label(task_frame, text="点击“下载”列可加入或移出本批次；选择任务后在右侧单独设置内容。", foreground="#666666", wraplength=500).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_selected)
        self.task_tree.bind("<Button-1>", self._toggle_task_participation)

        detail_frame = ttk.LabelFrame(workspace, text="任务详情", padding=10)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(4, weight=1)
        workspace.add(detail_frame, weight=1)

        ttk.Label(detail_frame, textvariable=self.detail_title_var, wraplength=520).grid(row=0, column=0, sticky="w")
        ttk.Label(detail_frame, textvariable=self.detail_url_var, foreground="#666666", wraplength=520).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(detail_frame, textvariable=self.detail_status_var).grid(row=2, column=0, sticky="w", pady=(3, 0))
        self.detail_error_label = ttk.Label(detail_frame, textvariable=self.detail_error_var, foreground="#b00020", wraplength=520)
        self.detail_error_label.grid(row=3, column=0, sticky="w", pady=(3, 6))

        format_frame = ttk.Frame(detail_frame)
        format_frame.grid(row=4, column=0, sticky="nsew")
        format_frame.columnconfigure(0, weight=1)
        format_frame.rowconfigure(0, weight=1)
        columns = ("kind", "format", "description", "details")
        self.format_tree = ttk.Treeview(format_frame, columns=columns, show="headings", height=8)
        headings = {"kind": "类型", "format": "格式 / 语言", "description": "编码 / 名称", "details": "分辨率 / 大小"}
        widths = {"kind": 55, "format": 110, "description": 185, "details": 120}
        for column in columns:
            self.format_tree.heading(column, text=headings[column])
            self.format_tree.column(column, width=widths[column], minwidth=55, stretch=column == "description")
        self.format_tree.grid(row=0, column=0, sticky="nsew")
        format_scrollbar = ttk.Scrollbar(format_frame, orient="vertical", command=self.format_tree.yview)
        format_scrollbar.grid(row=0, column=1, sticky="ns")
        self.format_tree.configure(yscrollcommand=format_scrollbar.set)

        selection_frame = ttk.LabelFrame(detail_frame, text="此网址的下载内容", padding=8)
        selection_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        selection_frame.columnconfigure(4, weight=1)
        self.participate_check = ttk.Checkbutton(selection_frame, text="参与下载", variable=self.participate_var, command=self._selection_changed)
        self.participate_check.grid(row=0, column=0, padx=(0, 16), sticky="w")
        self.video_check = ttk.Checkbutton(selection_frame, text="视频 MP4", variable=self.video_var, command=self._selection_changed)
        self.video_check.grid(row=0, column=1, padx=(0, 16), sticky="w")
        self.audio_check = ttk.Checkbutton(selection_frame, text="音频 M4A", variable=self.audio_var, command=self._selection_changed)
        self.audio_check.grid(row=0, column=2, padx=(0, 16), sticky="w")
        self.subtitle_check = ttk.Checkbutton(selection_frame, text="字幕 VTT", variable=self.subtitle_var, command=self._selection_changed)
        self.subtitle_check.grid(row=1, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        self.subtitle_combo = ttk.Combobox(selection_frame, textvariable=self.subtitle_language_var, width=13, state="disabled")
        self.subtitle_combo.grid(row=1, column=1, padx=(0, 16), pady=(8, 0), sticky="w")
        self.subtitle_combo.bind("<<ComboboxSelected>>", self._selection_changed)
        self.transcript_check = ttk.Checkbutton(selection_frame, text="Whisper 生成 VTT", variable=self.transcript_var, command=self._selection_changed)
        self.transcript_check.grid(row=2, column=0, columnspan=2, pady=(8, 0), sticky="w")
        ttk.Label(selection_frame, text="模型：").grid(row=2, column=2, pady=(8, 0), sticky="e")
        self.whisper_model_combo = ttk.Combobox(
            selection_frame, textvariable=self.whisper_model_var, values=WHISPER_MODELS, width=13, state="disabled"
        )
        self.whisper_model_combo.grid(row=2, column=3, padx=(0, 8), pady=(8, 0), sticky="w")
        self.whisper_model_combo.bind("<<ComboboxSelected>>", self._selection_changed)

        action_frame = ttk.Frame(container)
        action_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        action_frame.columnconfigure(0, weight=1)
        self.download_button = ttk.Button(action_frame, text="下载勾选任务", command=self._start_download)
        self.download_button.grid(row=0, column=1, sticky="e")

        self.progress = ttk.Progressbar(container, mode="determinate", maximum=100)
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        ttk.Label(container, textvariable=self.status_var, wraplength=1120).grid(row=7, column=0, columnspan=3, sticky="w")

        self.url_text.focus_set()
        self.url_text.bind("<Control-Return>", self._analyse_from_shortcut)

    def _analyse_from_shortcut(self, _event: tk.Event) -> str:
        if not self.busy:
            self._start_analysis()
        return "break"

    def _choose_output_directory(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get() or str(DEFAULT_OUTPUT_DIR))
        if selected:
            self.output_var.set(selected)

    def _choose_ffmpeg(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 ffmpeg.exe",
            filetypes=(("FFmpeg", "ffmpeg.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")),
        )
        if selected:
            self.ffmpeg_var.set(selected)

    def _read_config(self) -> AppConfig:
        output_value = self.output_var.get().strip()
        if not output_value:
            raise ValueError("请选择保存目录。")
        ffmpeg_value = self.ffmpeg_var.get().strip()
        if ffmpeg_value and not Path(ffmpeg_value).is_file():
            raise ValueError("指定的 FFmpeg 文件不存在。")
        return AppConfig(
            output_dir=Path(output_value),
            ffmpeg_path=ffmpeg_value or None,
            browser_cookies="chrome" if self.chrome_cookies_var.get() else None,
        )

    def _logger_for(self, config: AppConfig) -> logging.Logger:
        return configure_logger(config.logs_dir, name=f"media_downloader.gui.{str(config.logs_dir).casefold()}")

    def _start_analysis(self) -> None:
        if self.busy:
            return
        try:
            config = self._read_config()
            config.ensure_directories()
            tasks = parse_tasks(self.url_text.get("1.0", "end"))
            logger = self._logger_for(config)
        except (ValueError, OSError) as exc:
            messagebox.showerror("无法解析", str(exc), parent=self.root)
            return

        self.tasks = tasks
        self.current_task_id = tasks[0].task_id if tasks else None
        self.media_browser_cookies = config.browser_cookies
        self._render_task_list()
        self._set_busy(True)
        self._set_progress(f"正在解析 {len(tasks)} 个网址…", 0.0)
        threading.Thread(target=self._analysis_worker, args=(tasks, config, logger), name="media-analysis", daemon=True).start()

    def _analysis_worker(self, tasks: list[BatchTask], config: AppConfig, logger: logging.Logger) -> None:
        try:
            completed = analyse_tasks(tasks, config, logger, self._queue_task_progress)
        except Exception as exc:
            logger.exception("批量媒体解析异常终止")
            self.events.put(("analysis_error", str(exc)))
        else:
            self.events.put(("analysis_complete", completed))

    def _start_download(self) -> None:
        if self.busy:
            return
        self._save_current_selection()
        try:
            config = self._read_config()
            config.ensure_directories()
            if config.browser_cookies != self.media_browser_cookies:
                raise ValueError("Chrome Cookie 选项已改变，请重新点击“批量解析”后再下载。")
            plan = build_download_plan(self.tasks)
            logger = self._logger_for(config)
        except (ValueError, OSError) as exc:
            messagebox.showwarning("无法开始下载", str(exc), parent=self.root)
            return

        self._set_busy(True)
        self._set_progress(f"正在准备 {len(plan)} 个下载任务…", 0.0)
        threading.Thread(target=self._download_worker, args=(plan, config, logger), name="media-download", daemon=True).start()

    def _download_worker(self, plan: list[BatchTask], config: AppConfig, logger: logging.Logger) -> None:
        try:
            completed = execute_tasks(plan, config, logger, self._queue_task_progress)
        except Exception as exc:
            logger.exception("批量下载异常终止")
            self.events.put(("download_error", str(exc)))
        else:
            self.events.put(("download_complete", completed))

    def _queue_task_progress(self, task: BatchTask, message: str, percent: float | None) -> None:
        self.events.put(("task_progress", (task, message, percent)))

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "task_progress":
                    task, message, percent = payload  # type: ignore[misc]
                    self._merge_task(task)
                    self._set_progress(f"{task_title(task)}：{message}", percent)
                elif event == "analysis_complete":
                    self.tasks = payload  # type: ignore[assignment]
                    self._set_busy(False)
                    self._render_task_list()
                    ready = sum(task.media is not None for task in self.tasks)
                    failed = len(self.tasks) - ready
                    self._set_progress(f"批量解析完成：成功 {ready} 个，失败 {failed} 个。请选择每个任务的下载内容。", 100.0)
                elif event == "analysis_error":
                    self._finish_with_error("批量解析失败", str(payload))
                elif event == "download_complete":
                    self._merge_completed_tasks(payload)  # type: ignore[arg-type]
                    self._set_busy(False)
                    self._render_task_list()
                    self._show_download_summary()
                elif event == "download_error":
                    self._finish_with_error("批量下载失败", str(payload))
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._drain_events)
        except tk.TclError:
            pass

    def _find_task(self, task_id: int | None) -> BatchTask | None:
        if task_id is None:
            return None
        return next((task for task in self.tasks if task.task_id == task_id), None)

    def _merge_task(self, incoming: BatchTask) -> None:
        for index, task in enumerate(self.tasks):
            if task.task_id == incoming.task_id:
                self.tasks[index] = incoming
                break
        else:
            return
        self._update_task_row(incoming)
        if incoming.task_id == self.current_task_id:
            self._load_task_detail(incoming)

    def _merge_completed_tasks(self, completed: list[BatchTask]) -> None:
        """Keep excluded and parse-failed rows while applying finished plan results."""
        completed_by_id = {task.task_id: task for task in completed}
        self.tasks = [completed_by_id.get(task.task_id, task) for task in self.tasks]

    def _task_row_values(self, task: BatchTask) -> tuple[str, str, str, str, str]:
        include = "☑" if task.selection.include and task.media else "☐"
        progress = "" if task.progress is None else f"{task.progress:.0f}%"
        return include, task_title(task), task_status_label(task), task_selection_label(task.selection), progress

    def _render_task_list(self) -> None:
        selected_id = self.current_task_id
        if selected_id is None and self.tasks:
            selected_id = self.tasks[0].task_id
        self._rendering_tasks = True
        try:
            for item in self.task_tree.get_children():
                self.task_tree.delete(item)
            for task in self.tasks:
                self.task_tree.insert("", "end", iid=str(task.task_id), values=self._task_row_values(task))
            if selected_id is not None and self._find_task(selected_id):
                self.current_task_id = selected_id
                self.task_tree.selection_set(str(selected_id))
                self.task_tree.focus(str(selected_id))
            else:
                self.current_task_id = None
        finally:
            self._rendering_tasks = False
        self._load_task_detail(self._find_task(self.current_task_id))

    def _update_task_row(self, task: BatchTask) -> None:
        iid = str(task.task_id)
        if self.task_tree.exists(iid):
            self.task_tree.item(iid, values=self._task_row_values(task))

    def _on_task_selected(self, _event: tk.Event) -> None:
        if self._rendering_tasks:
            return
        self._save_current_selection()
        selected = self.task_tree.selection()
        if not selected:
            return
        self.current_task_id = int(selected[0])
        self._load_task_detail(self._find_task(self.current_task_id))

    def _toggle_task_participation(self, event: tk.Event) -> str | None:
        if self.busy or self.task_tree.identify_column(event.x) != "#1":
            return None
        iid = self.task_tree.identify_row(event.y)
        if not iid:
            return None
        self._save_current_selection()
        task = self._find_task(int(iid))
        if task is None or task.media is None:
            return "break"
        selection = task.selection
        task.selection = TaskSelection(
            include=not selection.include,
            video=selection.video,
            audio=selection.audio,
            subtitle_language=selection.subtitle_language,
            transcript=selection.transcript,
            transcript_model=selection.transcript_model,
        )
        self._update_task_row(task)
        if task.task_id == self.current_task_id:
            self._load_task_detail(task)
        return "break"

    def _load_task_detail(self, task: BatchTask | None) -> None:
        self._loading_detail = True
        try:
            for item in self.format_tree.get_children():
                self.format_tree.delete(item)
            if task is None:
                self.detail_title_var.set("选择一个任务查看详情")
                self.detail_url_var.set("-")
                self.detail_status_var.set("-")
                self.detail_error_var.set("")
                self.participate_var.set(False)
                self.video_var.set(False)
                self.audio_var.set(False)
                self.subtitle_var.set(False)
                self.subtitle_language_var.set("")
                self.transcript_var.set(False)
                self.whisper_model_var.set("base")
                self.subtitle_combo.configure(values=())
                return

            self.detail_title_var.set(task_title(task))
            self.detail_url_var.set(task.url)
            self.detail_status_var.set(f"状态：{task_status_label(task)}")
            errors = [error for error in (task.error, *task.errors) if error]
            self.detail_error_var.set("\n".join(dict.fromkeys(errors)))
            selection = task.selection
            self.participate_var.set(selection.include)
            self.video_var.set(selection.video)
            self.audio_var.set(selection.audio)
            self.subtitle_var.set(bool(selection.subtitle_language))
            self.subtitle_language_var.set(selection.subtitle_language or "")
            self.transcript_var.set(selection.transcript)
            self.whisper_model_var.set(selection.transcript_model)
            languages = subtitle_languages(task.media) if task.media else []
            self.subtitle_combo.configure(values=languages)
            if task.media:
                for row in media_table_rows(task.media):
                    self.format_tree.insert("", "end", values=row)
        finally:
            self._loading_detail = False
            self._refresh_detail_controls()

    def _save_current_selection(self) -> None:
        if self._loading_detail or self.busy:
            return
        task = self._find_task(self.current_task_id)
        if task is None or task.media is None:
            return
        task.selection = TaskSelection(
            include=self.participate_var.get(),
            video=self.video_var.get(),
            audio=self.audio_var.get(),
            subtitle_language=self.subtitle_language_var.get().strip() if self.subtitle_var.get() else None,
            transcript=self.transcript_var.get(),
            transcript_model=self.whisper_model_var.get().strip() or "base",
        )
        self._update_task_row(task)

    def _selection_changed(self, _event: tk.Event | None = None) -> None:
        if self._loading_detail or self.busy:
            return
        self._save_current_selection()
        self._refresh_detail_controls()

    def _refresh_detail_controls(self) -> None:
        task = self._find_task(self.current_task_id)
        media = task.media if task else None
        editable = media is not None and not self.busy
        has_video = bool(media and media.video_formats)
        has_audio = bool(media and media.audio_formats)
        has_subtitles = bool(media and media.subtitles)

        self.participate_check.configure(state="normal" if editable else "disabled")
        self.video_check.configure(state="normal" if editable and has_video else "disabled")
        self.audio_check.configure(state="normal" if editable and has_audio else "disabled")
        self.subtitle_check.configure(state="normal" if editable and has_subtitles else "disabled")
        self.subtitle_combo.configure(state="readonly" if editable and has_subtitles and self.subtitle_var.get() else "disabled")
        self.transcript_check.configure(state="normal" if editable and has_audio else "disabled")
        self.whisper_model_combo.configure(state="readonly" if editable and has_audio and self.transcript_var.get() else "disabled")
        can_download = any(task.media and task.selection.include for task in self.tasks)
        self.download_button.configure(state="normal" if can_download and not self.busy else "disabled")

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        for entry in (self.url_text, self.output_entry, self.ffmpeg_entry):
            entry.configure(state=state)
        for button in (self.analyse_button, self.output_button, self.ffmpeg_button):
            button.configure(state=state)
        self.chrome_cookies_check.configure(state=state)
        self._refresh_detail_controls()

    def _show_download_summary(self) -> None:
        succeeded = 0
        failed = 0
        lines: list[str] = []
        labels = {"video": "视频", "audio": "音频", "subtitle": "字幕", "transcript": "Whisper 字幕"}
        for task in self.tasks:
            lines.append(f"{task.task_id}. {task_title(task)} — {task_status_label(task)}")
            for result in task.results:
                succeeded += 1
                lines.append(f"  已保存 {labels.get(result.kind, result.kind)}：{result.path}")
            task_errors = [error for error in (task.error, *task.errors) if error]
            for error in dict.fromkeys(task_errors):
                failed += 1
                lines.append(f"  失败：{error}")
        self._set_progress(f"下载完成：成功 {succeeded} 项，失败 {failed} 项。", 100.0 if succeeded else 0.0)
        self._show_result_window("批量下载结果", "\n".join(lines) or "没有可执行的下载任务。")

    def _show_result_window(self, title: str, text: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("820x480")
        window.minsize(560, 300)
        frame = ttk.Frame(window, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        output = tk.Text(frame, wrap="word", state="normal")
        output.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        output.configure(yscrollcommand=scrollbar.set)
        output.insert("1.0", text)
        output.configure(state="disabled")
        ttk.Button(frame, text="关闭", command=window.destroy).grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))

    def _finish_with_error(self, title: str, detail: str) -> None:
        self._set_busy(False)
        self._set_progress(detail, 0.0)
        messagebox.showerror(title, detail, parent=self.root)

    def _set_progress(self, message: str, percent: float | None) -> None:
        self.status_var.set(message)
        if percent is None:
            if not self._indeterminate:
                self.progress.configure(mode="indeterminate")
                self.progress.start(10)
                self._indeterminate = True
            return
        if self._indeterminate:
            self.progress.stop()
            self._indeterminate = False
        self.progress.configure(mode="determinate", maximum=100, value=max(0.0, min(100.0, percent)))


def main() -> int:
    root = tk.Tk()
    MediaDownloaderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
