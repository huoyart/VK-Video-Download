# VK 视频下载器更新报告

更新日期：2026-10-04

## 本次更新

- **系统代理**：提供系统代理、直连、自定义代理三种选择。任务提交时保存代理设置，失败后手动重试可使用当前界面代理。
- **暂停与续传**：普通 HTTP 视频采用可续写的 `.vkparts` 分片；HLS/DASH 保留 yt-dlp 的 `.part` / `.ytdl` 续传文件。暂停不会删除未完成数据。
- **并发下载与合并**：支持 1、2、4、8 路并发。普通 HTTP 文件大于 64 MiB 且服务器支持 Range 时按 64 MiB 切片，下载后按序校验、合并；HLS/DASH 使用站点原生分片，分离音视频依赖 FFmpeg 合并。
- **速度与界面**：修正 Range 下载速度、剩余时间估算；任务失败后提供重试入口；列表改用中性选中样式，去除大面积蓝色高亮。
- **解析与网络恢复**：修正播放列表解析路径。Range 探测遇临时故障会重试，不再把超时误判为“不支持分片”；单片遇 503 或断流会从已保存字节重试。yt-dlp 网络读超时设为 40 秒，下载、分片及解析重试次数设为 5；任务自动重试时降低分片并发数。
- **Windows 启动**：新增 `run.cmd`，以同一 Python 解释器检查依赖、按需安装并启动，避免 `.py` 文件关联到另一套 Python。日志文件是文本，不应当作为 Python 脚本执行。

## 使用与升级

在项目目录的 Windows CMD 中运行：

```cmd
run.cmd --check
run.cmd
```

缺少依赖时运行 `run.cmd --install`，或使用同一个 `python` 执行 `python -m pip install -r requirements.txt`。已存在的下载文件及 `.vkparts` 不会在程序启动时被清理。需要合并分离音视频时，请确保 FFmpeg 在 PATH 中。

## 验证结果

- `python -m unittest discover -s tests -q`：28 项通过。
- `python -m py_compile vk_video_download.py download_core.py download_utils.py range_download.py`：通过。
- `run.cmd --check`：确认当前 Python 和依赖可用，不访问下载站点。
- 本地 HTTP 故障注入：探测 503/超时、分片 503/断流、模拟解析超时等 5 项新测试从修复前的 0/5 提升为 5/5。

这些测试使用本地 HTTP 服务及模拟下载器，没有验证真实 VK 视频的访问权限、站点格式或 CDN 可达性。若仍有特定视频失败，请提供链接和对应时间段的错误日志，以便定位站点返回内容或网络路径。
