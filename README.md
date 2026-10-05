# VK 视频下载器 2.2（中文版）

更新日期：2026-10-05

[查看更新报告](UPDATE_REPORT.md)

基于 [原项目](https://github.com/blyamur/VK-Video-Download) 的 Python / Tkinter 图形界面，使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下载 VK 及 yt-dlp 支持的平台视频。本项目仅供非商业个人使用，遵循原项目条款。

## 安装与启动

建议 Python 3.10 或更新版本。Windows CMD 下推荐用 `run.cmd` 启动：它始终使用同一条 `python` 命令检查依赖、安装依赖（仅在显式要求时）和运行程序，不依赖 `.py` 文件关联。

在包含 `run.cmd` 的项目目录打开 CMD，执行：

```cmd
run.cmd --check
run.cmd
```

`run.cmd --check` 仅检查本地 Python 与依赖，不访问网络、不启动下载器。已安装依赖时直接执行 `run.cmd`；缺依赖时可先运行 `python -m pip install -r requirements.txt`，或运行 `run.cmd --install` 自动安装后启动。若 `python` 命令不存在，请安装 Python 并将其加入 PATH。不要在 CMD 中直接输入 `vk_video_download.py`：Windows 的 `.py` 文件关联可能指向另一套 Python，从而报 `ModuleNotFoundError: No module named 'yt_dlp'`。

音视频分离的格式合并需要 [FFmpeg](https://ffmpeg.org/download.html) 在 `PATH` 中。命令 `ffmpeg -version` 可检查。没有 FFmpeg 时，单文件格式仍可能下载成功，但需要合并的格式会报错。

## 使用

1. 将一个或多个链接粘贴到输入框；换行、空格、英文或中文逗号均可分隔。按 **Ctrl+Enter** 或点击「下载视频」。
2. 可选随机文件名、50 字符标题上限、每视频独立文件夹；可选择下载目录。
3. **代理**有三种语义：「系统代理」不向 yt-dlp 显式传 `proxy`，使用其环境/系统代理发现；「不使用代理」传空代理值，明确直连；也可输入 HTTP/HTTPS/SOCKS5/SOCKS5H 代理。提交时保存代理设置快照；手动点击「重试」时采用界面当前选择的代理。
4. 「下载线程」可选 **1、2、4、8**，默认 4；「自动分片（目标64 MiB）」默认勾选。普通 HTTP 媒体在服务器返回可靠的 `206 Content-Range` 和文件大小时，按下述规则均匀分片，**小文件也使用 Range**；取消勾选则保留旧的固定 64 MiB 模式。分片下载后校验长度、按序合并并原子落盘。HLS/DASH 始终使用站点原生分片，下载线程选项设置 yt-dlp 的并发片数，分离音视频由 FFmpeg 合并。服务器不支持 Range 时自动交回 yt-dlp 下载。应用最多并行处理 3 个视频。
5. 点击表格「操作」列的「暂停」或「继续」。暂停在下一个下载进度回调生效；解析视频信息或后处理期间可能需要等待，合并时不提供暂停。普通 HTTP Range 模式保留任务独立的 `.vkparts` 目录，继续时跳过已完成分片并续写未完成分片。若媒体签名 URL 更新但文件大小不变，程序会抽样核对已有分片的头尾数据，再决定是否继续；内容变更会停止续传，避免混合不同视频。HLS/DASH 模式保留 `.part` / `.ytdl` 和固定输出模板，并启用 yt-dlp 的 `continuedl`。
6. 站点临时错误会自动重试一次；失败任务的「操作」列会显示「重试」，使用相同输出模板和已有分片重新尝试。可双击任务查看错误类别及实际分片数、实际工作线程、最大片大小（字节数）；尚未获得或值为 0 的分片信息不显示。单击任务「状态」列右侧的「取消」文字并确认，才会取消。取消与暂停不同：取消任务不可从界面继续，未完成文件不会被自动删除。
7. 「清除已结束」仅清除任务列表，不删除下载文件。退出时会请求取消未结束任务并等待当前网络/后处理调用退出。

下载内容默认保存到程序旁的 `downloads` 文件夹。任务完成状态只会在下载和 FFmpeg 后处理都成功返回后显示。

## 自动分片规则与旧布局兼容

以下规则仅适用于普通 HTTP Range 媒体的新布局；`size_bytes` 是正整数文件字节数，`threads` 是本次有效下载线程上限（自动重试可能降低该值）：

```text
target = 64 * 1024 * 1024 字节
count = min(size_bytes, max(threads, ceil(size_bytes / target)))
```

文件均匀分为 `count` 片，各片字节数最多相差 1；最大片字节数为 `ceil(size_bytes / count)`。新文件至少有有效线程数对应的分片，片数不超过字节数，每片至少 1 字节；实际工作线程上限为 `min(threads, count)`，详情中的 `active_workers` 显示核心提供的该值，而不是实时正在传输的片数。64 MiB 是目标大小，不代表每片必须恰好为 64 MiB。

| 文件大小 | 有效线程上限 | 新布局片数 | 最大片大小 |
| --- | --- | --- | --- |
| 10 MiB | 4 | 4 | 2.5 MiB |
| 160 MiB | 8 | 8 | 20 MiB |
| 1 GiB | 4 | 16 | 64 MiB |
| 1 字节 | 8（受文件大小限制） | 1 | 1 字节 |

- **取消自动分片**：新任务沿用旧规则；大于 64 MiB 的普通 HTTP 文件按固定 64 MiB 切片（末片可更小），不大于 64 MiB 时交给 yt-dlp 下载。
- **已有布局不重算**：Range 布局保存在任务 `.vkparts` 的 manifest 中。旧 manifest 继续采用原有固定布局；暂停继续、失败重试或降低线程数均沿用已保存的边界，不按当前自动分片开关或线程数重新切片，避免已有分片错位。
- **HLS/DASH 不重新切片**：自动分片开关不改变站点原生媒体片的数量和大小，仍由 yt-dlp 下载及续传。

自动分片用于匹配文件布局与下载并发，**不承诺提速**；实际速度取决于站点/CDN、网络、代理及磁盘等条件。

## 日志与网络故障

`vk_video_download.log` 是文本日志，不是 Python 脚本。请在项目目录的 Windows CMD 中用 `notepad vk_video_download.log` 打开，或用 `type vk_video_download.log` 查看；不要执行 `python vk_video_download.log`，否则文本日志会被当成 Python 代码而报 `IndentationError`。

如果日志显示 `vk.com` 元数据解析超时，或 `vkvd*.okcdn.ru` 下载 `Read timed out`，先检查该域名的网络连通性及界面的代理选择；需要代理时选择可用的系统代理或填写自定义代理。随后对失败任务点击「重试」，新任务必要时将下载线程降为 1 或 2，并避开不稳定网络；当前任务的自动重试也可能降低线程数，但不会重算已有分片布局。反复超时也可能是站点/CDN 暂时不可达；登录权限、失效链接或站点格式变化则需要分别排查，重试并不保证成功。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m py_compile vk_video_download.py download_core.py download_utils.py range_download.py
```

测试使用模拟 yt-dlp 下载器和本地 HTTP Range 服务，不访问真实站点。涉及 VK 登录权限、网站格式变化与真实续传速度时，应在本人可访问的视频上进行实际验证。

## Windows 发行包

在 Windows x64 开发环境安装 PyInstaller 后运行 `python build_release.py`。脚本在 `release/` 生成 `VK-Video-Downloader-2.2-win64.exe`、`VK-Video-Downloader-2.2-win64.zip` 和 `VK-Video-Downloader-2.2-win64-SHA256SUMS.txt`；EXE 无控制台窗口。2.2 使用独立产物名，不覆盖已有 2.1 EXE、ZIP 或其 `SHA256SUMS.txt`。`python build_release.py --verify-only` 校验 2.2 产物；程序资源、下载目录与日志位置保持不变。发行包不内置 FFmpeg，需要合并音视频时仍需在目标电脑安装 FFmpeg 并加入 `PATH`。

## 致谢

原版作者 [blyamur](https://github.com/blyamur)，下载核心 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。
