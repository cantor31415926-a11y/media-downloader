from pathlib import Path

import pytest

from utils.filename import sanitize_filename, unique_path


def test_sanitize_replaces_windows_invalid_characters() -> None:
    assert sanitize_filename('a<b>:c/d\\e|f?g*h"i') == "a_b__c_d_e_f_g_h_i"


def test_sanitize_keeps_chinese_and_unicode() -> None:
    assert sanitize_filename("测试：标题 🎬") == "测试：标题 🎬"


def test_sanitize_handles_blank_and_reserved_names() -> None:
    assert sanitize_filename(" . ") == "untitled"
    assert sanitize_filename("CON.txt") == "_CON.txt"


def test_sanitize_truncates_and_rejects_invalid_limit() -> None:
    assert len(sanitize_filename("a" * 30, max_length=10)) == 10
    with pytest.raises(ValueError):
        sanitize_filename("a", max_length=0)


def test_unique_path_never_overwrites(tmp_path: Path) -> None:
    first = unique_path(tmp_path, "标题", ".mp4")
    first.touch()
    second = unique_path(tmp_path, "标题", ".mp4")
    assert first.name == "标题.mp4"
    assert second.name == "标题 (1).mp4"
