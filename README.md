# VK 视频下载器（中文版）

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
4. 「分片并发」可选 1、2、4、8。对普通 HTTP 媒体，若服务器返回可靠的 `206 Content-Range` 且文件大于 64 MiB，就按 **64 MiB/片** 切分，片数随文件大小增加，并发抓取后校验长度、按序合并并原子落盘；并发数最多为选定值。对 HLS/DASH 则遵循站点原生分片大小，用该选项设置 yt-dlp 的并发片数，分离音视频由 FFmpeg 合并。服务器不支持 Range 时自动交回 yt-dlp 下载。应用最多并行处理 3 个视频。
5. 点击表格「操作」列的「暂停」或「继续」。暂停在下一个下载进度回调生效；解析视频信息或后处理期间可能需要等待，合并时不提供暂停。普通 HTTP Range 模式保留任务独立的 `.vkparts` 目录，继续时跳过已完成分片并续写未完成分片。若媒体签名 URL 更新但文件大小不变，程序会抽样核对已有分片的头尾数据，再决定是否继续；内容变更会停止续传，避免混合不同视频。HLS/DASH 模式保留 `.part` / `.ytdl` 和固定输出模板，并启用 yt-dlp 的 `continuedl`。
6. 站点临时错误会自动重试一次；失败任务的「操作」列会显示「重试」，使用相同输出模板和已有分片重新尝试。可双击任务查看错误类别。单击任务「状态」列右侧的「取消」文字并确认，才会取消。取消与暂停不同：取消任务不可从界面继续，未完成文件不会被自动删除。
7. 「清除已结束」仅清除任务列表，不删除下载文件。退出时会请求取消未结束任务并等待当前网络/后处理调用退出。

下载内容默认保存到程序旁的 `downloads` 文件夹。任务完成状态只会在下载和 FFmpeg 后处理都成功返回后显示。

## 日志与网络故障

`vk_video_download.log` 是文本日志，不是 Python 脚本。请在项目目录的 Windows CMD 中用 `notepad vk_video_download.log` 打开，或用 `type vk_video_download.log` 查看；不要执行 `python vk_video_download.log`，否则文本日志会被当成 Python 代码而报 `IndentationError`。

如果日志显示 `vk.com` 元数据解析超时，或 `vkvd*.okcdn.ru` 下载 `Read timed out`，先检查该域名的网络连通性及界面的代理选择；需要代理时选择可用的系统代理或填写自定义代理。随后对失败任务点击「重试」，必要时将分片并发降为 1 或 2，并避开不稳定网络。反复超时也可能是站点/CDN 暂时不可达；登录权限、失效链接或站点格式变化则需要分别排查，重试并不保证成功。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m py_compile vk_video_download.py download_core.py download_utils.py range_download.py
```

测试使用模拟 yt-dlp 下载器和本地 HTTP Range 服务，不访问真实站点。涉及 VK 登录权限、网站格式变化与真实续传速度时，应在本人可访问的视频上进行实际验证。

## 致谢

原版作者 [blyamur](https://github.com/blyamur)，下载核心 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。
