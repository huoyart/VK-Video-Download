# VK 视频下载器 2.2 · Windows x64 便携版

发布日期：2026-10-05

解压 `VK-Video-Downloader-2.2-win64.zip`，双击其中的 `VK-Video-Downloader-2.2-win64.exe`。这是无控制台窗口的独立 EXE，不要求用户预装 Python、yt-dlp 或 requests。请将程序放在可写目录；默认下载文件夹和运行日志位于 EXE 旁边，APP/资源及数据路径与旧版一致。

## 2.2 新增自动分片

- 「自动分片（目标64 MiB）」默认勾选；下载线程可选 1、2、4、8，默认 4，分片选项独立一行。
- 新的普通 HTTP Range 布局采用 `count = min(size_bytes, max(threads, ceil(size_bytes / (64 * 1024 * 1024))))` 并均匀划分，各片字节数最多相差 1。新文件至少分为有效线程数对应的片数（不超过文件字节数），自动模式小文件也使用 Range。例如 10 MiB / 4 线程分为 4 片（每片 2.5 MiB），160 MiB / 8 线程分为 8 片（每片 20 MiB）。
- 取消勾选后，新任务保留旧固定 64 MiB 模式：普通 HTTP 文件大于 64 MiB 才使用固定片大小，末片可更小。
- Range manifest 保存布局；旧 manifest 继续使用原有固定布局，暂停续传、重试或降低线程数不重算边界。HLS/DASH 始终采用站点原生分片，不受自动切片规则影响。
- 双击任务可查看实际分片数、实际工作线程和最大片字节数；值为 0 或尚未获得时省略。自动分片不承诺提速。

本版保留系统/直连/自定义代理、暂停续传、失败重试、速度与剩余时间显示及中性任务列表样式。详细变更见 `UPDATE_REPORT.md`。

**音视频合并**：发行包没有内置 FFmpeg。需要下载分离音视频格式时，请自行安装 FFmpeg 并将 `ffmpeg.exe` 加入 `PATH`；不需要合并的单文件格式仍可下载。运行环境为 Windows 10/11 x64。

`VK-Video-Downloader-2.2-win64-SHA256SUMS.txt` 提供 2.2 EXE 和 ZIP 校验值。2.2 使用独立文件名，不覆盖已有 2.1 EXE、ZIP 及 `SHA256SUMS.txt`；`python build_release.py --verify-only` 校验 2.2 产物。

验证：45 项单元/GUI/本地 HTTP 测试通过，1,000 组布局检查通过；构建脚本校验 Windows GUI 子系统、ZIP 完整性、文档一致性及 SHA-256，ZIP 解压后的 EXE 已验证 GUI 正常打开和关闭。详细记录见 `UPDATE_REPORT.md`。真实 VK 站点权限、格式与 CDN 可达性不在离线测试范围内。
