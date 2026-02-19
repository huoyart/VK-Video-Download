# VK 视频下载器 - 中文版

![Version](https://img.shields.io/badge/version-2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.6+-green.svg)
![License](https://img.shields.io/badge/license-Non--commercial-red.svg)

<img width="816" height="469" alt="image" src="https://github.com/user-attachments/assets/ca2b96ad-ded8-4355-87bd-6b28b2692363" />


## 📖 简介

这是一个基于 Python 3 的图形界面视频下载工具，支持从 VK.com 以及其他多个视频平台下载视频。本版本是在 [原版 VK-Video-Download](https://github.com/blyamur/VK-Video-Download) 基础上进行中文化并添加新功能的改进版本。

## ✨ 主要特性

- 🎬 **多平台支持**：支持 VK.com、Rutube、Mail.Ru、YouTube 等多个视频平台
- 📥 **批量下载**：支持同时下载多个视频（最多 3 个并行下载）
- 📊 **实时进度**：显示下载进度、速度、剩余时间和文件大小
- 🎯 **灵活命名**：支持随机文件名、限制文件名长度、为每个视频创建单独文件夹
- 🚫 **取消下载**：可以随时取消正在进行的下载任务
- 🌐 **代理支持**：内置代理设置功能，支持 HTTP/HTTPS/SOCKS5 代理
- 🎨 **现代界面**：基于 Tkinter 的现代化图形界面
- 📝 **日志记录**：自动记录下载日志到文件

## 🔄 与原版的对比

### 主要修改

1. **完全中文化**
   - ✅ 所有用户界面文本翻译为中文
   - ✅ 所有代码注释翻译为中文
   - ✅ 所有日志消息翻译为中文
   - ✅ 所有错误提示翻译为中文

2. **新增代理功能** ⭐
   - ✅ 添加了代理设置下拉菜单
   - ✅ 支持预设代理选项（HTTP、SOCKS5）
   - ✅ 支持自定义代理输入
   - ✅ 代理设置应用于视频信息获取和下载过程

3. **界面优化**
   - ✅ 优化了代理设置区域的布局
   - ✅ 改进了状态栏显示
   - ✅ 优化了表格列标题显示

4. **代码改进**
   - ✅ 改进了跨平台图标加载（支持 Windows .ico 和 Linux .png）
   - ✅ 优化了错误处理机制
   - ✅ 改进了日志记录格式

### 保留的原版功能

- ✅ 多任务并行下载（最多 3 个）
- ✅ 下载进度实时显示
- ✅ 下载任务取消功能
- ✅ 临时文件自动清理
- ✅ 文件名安全处理
- ✅ 右键菜单支持
- ✅ 快捷键支持（Ctrl+C/V/X/A）
- ✅ 主题支持（Sun-Valley/Spring-Noon）

## 🚀 快速开始

### 环境要求

- Python 3.6 或更高版本
- 操作系统：Windows / Linux / macOS

### 安装步骤

1. **克隆或下载项目**
<<<<<<< HEAD
   ```bash
   git clone https://github.com/your-username/VK-Video-Download.git
=======

   ```bash
   git clone https://github.com/huoyart/VK-Video-Download.git
>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
   cd VK-Video-Download
   ```

2. **安装依赖**
<<<<<<< HEAD
   ```bash
   pip install -r requirements.txt
   ```
   
   或者单独安装 yt-dlp：
=======

   ```bash
   pip install -r requirements.txt
   ```

   或者单独安装 yt-dlp：

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
   ```bash
   pip install yt-dlp
   ```

3. **运行程序**
<<<<<<< HEAD
=======

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
   ```bash
   python vk_video_download.py
   ```

### 使用方法

1. 启动程序后，在输入框中粘贴视频链接（支持多个链接，用逗号分隔）
2. 可选设置：
   - 选择是否使用随机文件名
   - 选择是否限制文件名长度为 50 个字符
   - 选择是否为每个视频创建单独文件夹
   - 选择代理设置（如果需要）
3. 点击"下载视频"按钮或按 Enter 键开始下载
4. 在下载列表中查看进度，可以点击 ✖ 按钮取消下载
5. 下载完成后，视频将保存在 `downloads` 文件夹中

### 支持的链接格式

- VK.com: `https://vk.com/video-100000000_100000000`
- VKVideo.ru: `https://vkvideo.ru/video-50804569_456239864`
- Mail.Ru: `https://my.mail.ru/v/hi-tech_mail/video/_groupvideo/437.html`
- Rutube: `https://rutube.ru/video/a16f1e575e114049d0e4d04dc7322667/`
- YouTube: `https://www.youtube.com/watch?v=...`
- 以及其他 yt-dlp 支持的平台

## ⚙️ 代理设置

本版本新增了代理设置功能，方便在需要代理访问的环境中下载视频。

### 代理选项

- **不使用代理**：默认选项，直接连接
- **预设代理**：包含常用的本地代理地址
  - `http://127.0.0.1:7890`
  - `http://127.0.0.1:10808`
  - `socks5://127.0.0.1:1080`
- **自定义代理**：可以输入任何符合 yt-dlp 格式的代理地址

### 代理格式

支持的代理格式：
<<<<<<< HEAD
=======

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
- HTTP: `http://proxy.example.com:8080`
- HTTPS: `https://proxy.example.com:8080`
- SOCKS5: `socks5://proxy.example.com:1080`

## 📁 项目结构

```
VK-Video-Download/
├── vk_video_download.py    # 主程序文件
├── requirements.txt         # Python 依赖列表
├── README_CN.md            # 中文说明文档（本文件）
├── README.md               # 原版英文说明文档
├── theme/                  # 主题文件夹
│   ├── vk_theme.tcl        # 主题样式文件
│   └── icon.ico            # 应用图标
└── downloads/              # 下载文件夹（自动创建）
```

## 🛠️ 构建可执行文件

使用 PyInstaller 打包为独立可执行文件：

```bash
pyinstaller vk_video_download.py --noconsole --onefile --icon=theme/icon.ico
```

## ❓ 常见问题

### Q: 视频无法下载？
<<<<<<< HEAD
A: 可能的原因：
=======

A: 可能的原因：

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
- 视频设置了访问权限，需要登录或授权
- 视频格式或来源不被 yt-dlp 支持
- 网络连接问题，尝试使用代理

### Q: 下载的文件扩展名是 .unknown_video？
<<<<<<< HEAD
A: 可以手动将文件重命名为 .mp4，通常可以正常播放

### Q: 如何查看下载日志？
A: 日志文件保存在程序目录下的 `vk_video_download.log`

### Q: 在 Linux 系统上图标不显示？
A: 程序会自动尝试加载 .png 格式的图标，如果仍无法显示，这是正常的，不影响使用

### Q: 可以同时下载多少个视频？
=======

A: 可以手动将文件重命名为 .mp4，通常可以正常播放

### Q: 如何查看下载日志？

A: 日志文件保存在程序目录下的 `vk_video_download.log`

### Q: 在 Linux 系统上图标不显示？

A: 程序会自动尝试加载 .png 格式的图标，如果仍无法显示，这是正常的，不影响使用

### Q: 可以同时下载多少个视频？

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
A: 默认最多同时下载 3 个视频，这是为了避免过载

## 📝 更新日志

### 版本 2.0（中文版）

**新增功能：**
<<<<<<< HEAD
=======

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
- ✨ 完全中文化界面和提示
- ✨ 新增代理设置功能
- ✨ 优化跨平台图标加载

**改进：**
<<<<<<< HEAD
=======

>>>>>>> cb37053091bfb150fd6bc434261d4ce1a8d4842a
- 🔧 改进错误处理机制
- 🔧 优化日志记录格式
- 🔧 改进界面布局

### 原版 2.0 功能

- 多任务并行下载列表显示
- 限制并行下载数量（最多 3 个）
- 下载任务取消功能
- 临时文件自动清理
- 下载进度统计（总数/完成数）
- 文件名和来源显示优化
- UI 更新优化（减少卡顿）

## 📄 许可证

**非商业用途，仅供个人使用**

本项目基于原版 [VK-Video-Download](https://github.com/blyamur/VK-Video-Download) 进行修改，遵循相同的许可证条款。

## 🙏 致谢

- 原版作者：[blyamur](https://github.com/blyamur)
- 基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 项目
- 使用 [Sun-Valley-ttk-theme](https://github.com/rdbende/Sun-Valley-ttk-theme) 主题

## 🔗 相关链接

- [原版项目](https://github.com/blyamur/VK-Video-Download)
- [yt-dlp 文档](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp 安装指南](https://github.com/yt-dlp/yt-dlp/wiki/Installation)

## 📧 反馈

如果您在使用过程中遇到问题或有改进建议，欢迎提交 Issue 或 Pull Request。

---

**注意**：本程序仅供学习和个人使用，请遵守相关网站的使用条款和版权规定。

© 2025 中文版 - 基于原版修改

