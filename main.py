
"""CLI entry point for downloading publicly accessible media users are authorized to save."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import AppConfig, DEFAULT_OUTPUT_DIR
from downloader.core import DownloadError, MediaDownloader
from downloader.extractor import MediaExtractionError, extract_media
from downloader.models import MediaInfo, format_filesize
from downloader.subtitle import SubtitleDownloadError, choose_subtitle, download_subtitle
from utils.logger import configure_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="下载您有权访问的公开网页视频、音频和字幕。")
    parser.add_argument("url", nargs="?", help="公开媒体网页 URL（http 或 https）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help=f"保存根目录，默认 {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--ffmpeg", help="ffmpeg.exe 的完整路径；未指定时从 PATH 查找")
    parser.add_argument(
        "--cookies-from-chrome",
        action="store_true",
        help="仅本次运行让 yt-dlp 读取本机 Chrome Cookie；不会导出或记录 Cookie",
    )
    return parser


def print_media_info(media: MediaInfo) -> None:
    print(f"\n标题：{media.title}")
    print(f"来源：{media.source_type}")
    print("视频格式：")
    for item in media.video_formats[:10]:
        print(f"  - {item.format_id}: {item.label}")
    if not media.video_formats:
        print("  - 无")
    print("音频格式：")
    for item in media.audio_formats[:10]:
        print(f"  - {item.format_id}: {item.label}")
    if not media.audio_formats:
        print("  - 无")
    print("字幕：")
    for subtitle in media.subtitles:
        print(f"  - {subtitle.language}: {subtitle.name} ({subtitle.extension or '未知格式'})")
    if not media.subtitles:
        print("  - 无")


def select_subtitle_language(media: MediaInfo) -> str | None:
    if not media.subtitles:
        print("未发现公开字幕。")
        return None
    language = input("输入要下载的字幕语言代码（例如 zh-CN，直接回车取消）：").strip()
    return language or None


def run_menu(media: MediaInfo, downloader: MediaDownloader, config: AppConfig) -> None:
    while True:
        print("\n请选择操作：1) 下载视频  2) 下载音频  3) 下载字幕  0) 退出")
        choice = input("> ").strip()
        try:
            if choice == "1":
                result = downloader.download_video(media)
                print(f"视频已保存：{result.path}")
            elif choice == "2":
                result = downloader.download_audio(media)
                print(f"音频已保存：{result.path}")
            elif choice == "3":
                language = select_subtitle_language(media)
                if language:
                    result = download_subtitle(media, choose_subtitle(media, language), config)
                    print(f"字幕已保存：{result.path}")
            elif choice == "0":
                return
            else:
                print("请输入 0、1、2 或 3。")
        except (DownloadError, SubtitleDownloadError) as exc:
            downloader.logger.error("下载失败：%s", exc)
            print(f"下载失败：{exc}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig(
        output_dir=args.output_dir,
        ffmpeg_path=args.ffmpeg,
        browser_cookies="chrome" if args.cookies_from_chrome else None,
    )
    try:
        config.ensure_directories()
    except OSError as exc:
        print(f"无法创建下载目录：{exc}", file=sys.stderr)
        return 2
    logger = configure_logger(config.logs_dir)
    url = args.url or input("请输入您有权访问的公开网页 URL：").strip()
    try:
        media = extract_media(
            url,
            timeout=config.request_timeout,
            logger=logger,
            browser_cookies=config.browser_cookies,
        )
    except (ValueError, MediaExtractionError) as exc:
        logger.error("媒体解析失败：%s", exc)
        print(f"媒体解析失败：{exc}", file=sys.stderr)
        return 1
    print_media_info(media)
    run_menu(media, MediaDownloader(config, logger), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
