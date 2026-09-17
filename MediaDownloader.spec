# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the Windows desktop application."""

from PyInstaller.utils.hooks import collect_all


datas = []
binaries = []
hiddenimports = []
for package in ("faster_whisper", "ctranslate2", "tokenizers", "opencc", "yt_dlp", "curl_cffi"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MediaDownloader",
    console=False,
)
