# Windows 本地媒体下载工具

一个面向 Windows 的 Python 本地工具，提供简单的图形界面和命令行模式，用于保存您有权访问的公开网页视频、音频与字幕。

程序优先使用 `yt-dlp` 提取常见媒体网站及公开媒体流；对未被识别的普通网页，扫描静态 HTML 中的 `video`、`audio`、`source` 和 `track` 标签。视频默认输出 MP4，音频默认输出 M4A，字幕默认输出 VTT。

> 合规边界：本工具不绕过 DRM、付费墙、登录、验证码、访问控制或任何技术保护措施。可由用户明确选择，让 yt-dlp 在当前运行中读取本机 Chrome Cookie，以访问用户本来就能正常访问的内容；Cookie 不会导出、保存或写入日志。请只下载您拥有权利或已获授权保存的资源。

## 项目结构

```text
.
├── gui.py                  # Tkinter 图形界面入口
├── start_gui.bat           # Windows 双击启动脚本
├── main.py                 # CLI 入口、菜单和错误提示
├── config.py               # 输出目录、超时、FFmpeg 配置
├── downloader/
│   ├── batch.py             # 批量任务状态、选择快照与顺序执行
│   ├── extractor.py         # yt-dlp / HTML 媒体信息解析
│   ├── core.py              # 视频、音频下载与 FFmpeg 处理
│   ├── subtitle.py          # 字幕下载与 VTT 转换
│   ├── transcriber.py       # Whisper 语音识别与 VTT 生成
│   └── models.py            # 统一媒体数据模型
├── utils/
│   ├── filename.py          # Windows 安全文件名与防覆盖
│   └── logger.py            # 控制台和文件日志
└── tests/                   # 不依赖网络的 pytest 测试
```

## 运行逻辑

1. 输入一个或多个 `http://` 或 `https://` 的公开网页 URL；图形界面中每行一个，空行会忽略，重复网址只保留首次出现的位置。
2. 图形界面按输入顺序逐个解析。每个网址都会成为独立任务；解析失败会显示在该任务中，不会中断后续网址。
3. 在任务列表选择一个已解析任务，查看其媒体格式，并为该网址单独选择视频、音频、网站字幕语言或 Whisper 字幕。
4. 选择参与下载的任务后，程序按任务列表顺序下载；同一任务中的视频、音频、网站字幕与 Whisper 字幕也依次处理。
5. 单个内容或单个网址失败时，程序记录错误并继续处理后续已选内容和任务。
6. 文件先下载到临时目录，再移动到最终目录。视频分离流由 yt-dlp/FFmpeg 合并为 MP4；音频提取为 M4A；非 VTT 字幕由 FFmpeg 转换为 VTT。
7. 页面没有独立字幕时，可用 faster-whisper 从临时音频生成 VTT；中文识别结果会自动转换为简体中文。
8. 标题会清理 Windows 非法字符，超长时截断；同名文件自动使用 ` (1)`、` (2)` 后缀，绝不覆盖已有文件。

## 安装 Python

需要 Python 3.11 或更高版本。请从 [Python 官方下载页](https://www.python.org/downloads/windows/) 安装，并在安装页面勾选 **Add Python to PATH**。

安装后新开 PowerShell，检查：

```powershell
py -3.11 --version
```

如果安装的是更新版本，例如 Python 3.12：

```powershell
py -3.12 --version
```

## 安装 FFmpeg

FFmpeg 用于合并视频音频、提取 M4A 和转换字幕格式。

1. 从 [FFmpeg 官方下载说明](https://ffmpeg.org/download.html) 选择 Windows 构建版本并解压，例如 `C:\ffmpeg`。
2. 将 `C:\ffmpeg\bin` 加入 Windows 的 `PATH` 环境变量。
3. 新开 PowerShell，确认：

```powershell
ffmpeg -version
```

也可以每次运行时传入 FFmpeg 可执行文件路径：

```powershell
py -3.12 main.py --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe"
```

程序还会自动查找 `保存目录\FFmpeg` 下同时包含 `ffmpeg.exe` 和 `ffprobe.exe` 的便携版，
例如 `D:\MediaDownloader\FFmpeg\ffmpeg-8.1.1-essentials_build\bin`，无需修改系统 `PATH`。

## 安装依赖

在项目目录中运行（Python 小版本按本机实际安装情况替换）：

```powershell
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements.txt
```

`faster-whisper` 会随依赖一起安装。首次生成 Whisper 字幕时，程序会把所选模型下载到
`保存目录\WhisperModels`；`base` 模型兼顾中文识别效果和普通 CPU 的运行速度，后续会复用缓存。

## 使用图形界面（推荐）

完成依赖安装后，直接双击项目目录中的：

```text
start_gui.bat
```

如果在 PyCharm 中运行：

1. 在项目窗口中打开 `gui.py`。
2. 确认项目解释器为 `.venv\Scripts\python.exe`。
3. 右键 `gui.py`，选择 **Run 'gui'**。

也可以在 PowerShell 中启动：

```powershell
.\.venv\Scripts\python.exe gui.py
```

界面使用步骤：

1. 将您有权访问的公开网页 URL 粘贴到“网页 URL”，每行一个；只下载一个网址时照常输入一行。
2. “保存到”默认是 `D:\MediaDownloader`；需要更换时点击“浏览”。
3. 程序会自动检查保存目录下的 `FFmpeg` 文件夹和系统 `PATH`；未检测到时点击“选择”指定 `ffmpeg.exe`。
4. 通常保持“使用 Chrome Cookie”关闭；如果网站明确要求 Cookie，并且该内容能在你的 Chrome 中正常访问，再勾选该选项。
5. 点击“批量解析”。每个输入网址都会出现在任务列表中，显示标题或网址、解析状态、下载选择和进度；某个网址失败不会中断其他网址。
6. 从任务列表选择一个成功解析的网址。右侧会显示该网址的媒体格式和下载内容；切换任务时，已经做出的选择会自动保留。
7. 对每个要下载的网址勾选“参与下载”，再按需选择“视频 MP4”“音频 M4A”或网站提供的“字幕 VTT”。下载网站字幕时还必须选择该网址可用的语言。
8. 页面有可用音频时，可为该网址勾选“Whisper 生成 VTT”并选择 `tiny`、`base` 或 `small`；模型越大通常越准确，但下载和识别越慢。
9. 点击“下载勾选任务”。程序按任务列表顺序依次下载，每个任务只执行自己的选择；完成后会列出已保存文件和失败原因。

Windows 可能在 Chrome 运行时锁定 Cookie 数据库。若提示“无法读取 Chrome Cookie 数据库”，请先在 Chrome 中确认链接可访问并复制 URL，然后完全退出 Chrome（包括后台进程），再回到下载器解析；下载完成前不要重新打开 Chrome。

视频使用最佳兼容格式；同一个任务同时勾选多个项目时，程序按视频、音频、网站字幕、Whisper 字幕的顺序下载。下载期间网址输入、保存目录、FFmpeg、Cookie 和下载选择会锁定，但仍可查看任务详情。某一项失败不会阻止后续项目，完成对话框会分别列出成功路径和失败原因。

## 使用命令行

交互式输入 URL：

```powershell
py -3.12 main.py
```

直接传入 URL：

```powershell
py -3.12 main.py "https://example.com/public-media-page"
```

需要在当前运行中使用 Chrome Cookie：

```powershell
py -3.12 main.py --cookies-from-chrome "https://example.com/media-page"
```

程序随后显示可用格式和字幕，输入：

- `1` 下载视频
- `2` 下载音频
- `3` 下载字幕，然后输入语言代码，例如 `zh-CN`
- `0` 退出

## 下载位置和修改方法

默认保存到：

```text
D:\MediaDownloader\
├── Video\
├── Audio\
├── Subtitle\
├── Temp\
└── Logs\
```

程序在开始解析或下载时自动创建 `Video`、`Audio`、`Subtitle`、`Temp` 和 `Logs`。如果将便携版
FFmpeg 放在 `D:\MediaDownloader\FFmpeg`，程序会自动查找其中的 `ffmpeg.exe` 与 `ffprobe.exe`；首次使用
Whisper 时会在 `D:\MediaDownloader\WhisperModels` 缓存所选模型。

临时更换保存目录：

```powershell
py -3.12 main.py --output-dir "D:\OtherMedia"
```

也可在 `config.py` 修改 `DEFAULT_OUTPUT_DIR`，作为长期默认目录。

## 测试

测试完全离线，不会访问真实网站：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 常见错误

### `No installed Python found` 或找不到 `python`

Python 启动器存在但没有实际 Python 解释器。安装 Python 3.11+ 后重新打开 PowerShell，再执行 `py -3.12 --version`（版本按实际安装版本调整）。

如果双击 `start_gui.bat` 后提示找不到 Python，请确认项目中的 `.venv` 已创建；也可以在项目目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### `未找到 FFmpeg`

将便携版解压到 `保存目录\FFmpeg`，或将其 `bin` 目录加入 PATH。图形界面中也可以点击 FFmpeg 右侧的“选择”指定 `ffmpeg.exe`；命令行模式可使用 `--ffmpeg`。

### `媒体解析失败`

确认 URL 是公开的 HTTP/HTTPS 网页；该页面可能需要登录、使用 DRM、仅有 JavaScript 动态播放器，或不允许公开提取。工具不会绕过这些限制。

### `Fresh cookies are needed` 或无法复制 Chrome Cookie 数据库

仅当该内容能够通过你自己的 Chrome 正常访问时，勾选“使用 Chrome Cookie（仅当前运行）”后重新解析。程序只把 Chrome Cookie 交给当前 yt-dlp 解析和下载流程，不会导出、保存或记录 Cookie 值。

如果提示无法复制 Cookie 数据库，请完全退出 Chrome（包括后台进程）后重试，并在下载完成前保持 Chrome 关闭。验证码、登录要求和其他访问控制仍需用户在官方网站中正常完成，工具不会绕过。

### `下载失败：HTTP 403/401`

资源不是匿名公开可访问，或者服务端拒绝该请求。对于你在 Chrome 中本来就有权访问的内容，可显式启用 Chrome Cookie；工具仍不会绕过访问控制。

### 找不到字幕或没有指定语言

该页面没有公开字幕，或可用语言与输入的语言代码不同。请先阅读程序显示的字幕语言列表；没有独立字幕时，可改用图形界面的“Whisper 生成 VTT”。

### Whisper 模型下载或识别失败

首次使用需要联网下载模型到 `保存目录\WhisperModels`。确认磁盘空间和网络正常，并优先使用 `base`；
普通 CPU 识别长视频可能需要较长时间。失败的临时音频会按配置保留在 `Temp`，详细原因记录在日志中。

### 文件名错误或文件已存在

程序会自动清理 Windows 非法字符并在重名时创建 `标题 (1).mp4` 等新文件，不会覆盖原有文件。确认目标磁盘有写入权限和足够空间。

### 查看详细日志

默认日志文件位于：

```text
D:\MediaDownloader\Logs\media_downloader.log
```

如果修改了保存目录，日志位于新目录的 `Logs\media_downloader.log`。下载或 FFmpeg 处理失败时，可在这里查看详细原因。
