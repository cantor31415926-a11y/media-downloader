# Media Downloader

> 一个 Windows 视频 / 音频 / 字幕下载工具，支持 yt-dlp、FFmpeg 和 Whisper。

下载你有权保存的公开网页媒体：视频会保存为 MP4，音频为 M4A，字幕为 VTT。图形界面支持多网址任务、网站字幕和 Whisper 语音识别字幕。

> 合规边界：本工具不绕过 DRM、付费墙、验证码、登录或其他访问控制。请只保存你拥有权利或已获授权的内容。

## 给普通用户

1. 打开仓库右侧的 [Releases](../../releases)，下载最新版本的 `MediaDownloader-Windows-x64.zip`。
2. 将压缩包解压到任意文件夹，双击 `MediaDownloader.exe`。
3. 粘贴公开网页 URL，选择保存位置，点击“批量解析”，然后选择下载内容。

程序默认把文件保存到 `%USERPROFILE%\Videos\MediaDownloader`，并自动按 `Video`、`Audio`、`Subtitle` 分类。可在界面中随时改为其他文件夹。

### 第一次使用前：FFmpeg

下载和保存某些媒体格式、合并视频音频、提取 M4A、转换字幕，都需要 FFmpeg。EXE 没有内置 FFmpeg，以便避免随应用再分发第三方二进制及其许可证义务。任选一种方式准备它：

- 将 FFmpeg 的 `bin` 目录加入 Windows 的 `PATH`；或
- 在界面中点击“选择”，指定 `ffmpeg.exe`；或
- 将同时包含 `ffmpeg.exe` 和 `ffprobe.exe` 的便携版放到保存位置的 `FFmpeg` 文件夹中。

可以从 [FFmpeg 下载页](https://ffmpeg.org/download.html) 选择 Windows 构建。没有 FFmpeg 时，程序会在需要时提示，不会静默失败。

首次使用 Whisper 字幕还会下载所选模型到 `WhisperModels` 文件夹；`base` 是速度与中文识别效果的默认折中。

## 给开发者

需要 Windows、Python 3.11+ 和 FFmpeg。克隆后在项目根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动 GUI：

```powershell
.\.venv\Scripts\python.exe gui.py
```

也可以双击 `start_gui.bat`。命令行模式：

```powershell
.\.venv\Scripts\python.exe main.py "https://example.com/public-media-page"
```

运行离线测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

本地构建 Windows 单文件程序：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean MediaDownloader.spec
```

输出文件为 `dist\MediaDownloader.exe`。它不包含 FFmpeg，也不会预装 Whisper 模型。

## 发布新版本

仓库已包含 GitHub Actions 发布流程。推送形如 `v1.0.0` 的 Git 标签后，它会在 Windows 环境中安装依赖、运行测试、打包 `MediaDownloader.exe`，并创建 GitHub Release，附带 `MediaDownloader-Windows-x64.zip`。

```powershell
git tag v1.0.0
git push origin v1.0.0
```

请先提交这次项目整理的改动，再创建标签。发布完成后，到仓库的 **Releases** 页面确认下载包和自动生成的发行说明。

## 功能

- 使用 yt-dlp 解析常见媒体站点；对普通静态网页扫描 `video`、`audio`、`source` 和 `track` 标签。
- 多网址按顺序解析与下载；单项失败不会终止其他任务。
- 视频 MP4、音频 M4A、网站字幕 VTT，以及 faster-whisper 生成的 VTT 字幕。
- 可选地在当前运行中读取本机 Chrome Cookie；Cookie 不会导出、保存或写入日志。
- 安全文件名与自动防覆盖；日志、临时文件和模型缓存按保存目录隔离。

## 项目结构

```text
.
├── gui.py                     # Tkinter 图形界面入口
├── main.py                    # 命令行入口
├── MediaDownloader.spec       # PyInstaller 单文件构建配置
├── requirements.txt           # 开发、测试和打包依赖
├── .github/workflows/
│   └── release.yml            # v* 标签触发的 Windows Release
├── downloader/                # 解析、下载、字幕与 Whisper 逻辑
├── utils/                     # 文件名和日志工具
└── tests/                     # 不访问真实网站的离线测试
```

## 常见问题

**Windows 显示“未知发布者”或阻止启动？** 这是未签名 EXE 的常见提示。请只从本仓库的 Releases 下载，并在确认来源后选择“更多信息”→“仍要运行”。

**“未找到 FFmpeg”？** 按上面的三种方式之一提供 `ffmpeg.exe` 和 `ffprobe.exe`，然后重新解析或下载。

**为什么媒体解析失败或返回 401/403？** 资源可能需要登录、受 DRM 保护、只能由动态播放器访问，或服务端禁止访问。工具不会绕过这些限制；仅当你本来能在 Chrome 正常访问时，才可选择“使用 Chrome Cookie（仅当前运行）”。

**日志在哪里？** 在选择的保存位置下的 `Logs\media_downloader.log`。

## 许可证

[MIT License](LICENSE)
