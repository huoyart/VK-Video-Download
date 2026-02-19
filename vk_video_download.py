import threading
import tkinter as tk
from tkinter import ttk, messagebox, Menu
import webbrowser
import yt_dlp
import requests
import logging
import os
import random
import string
from logging.handlers import RotatingFileHandler
import datetime
import re
import sys
import time
import platform

# https://vk.com/video-87011294_456249654     | example for vk.com
# https://vkvideo.ru/video-50804569_456239864     | example for vkvideo.ru
# https://my.mail.ru/v/hi-tech_mail/video/_groupvideo/437.html     | example for my.mail.ru
# https://rutube.ru/video/a16f1e575e114049d0e4d04dc7322667/     | example for rutube.ru
# FromRussiaWithLove | Mons (https://github.com/blyamur/VK-Video-Download/)  | ver. 1.8 | "non-commercial use only, for personal use"

# 日志设置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('vk_video_download.log', maxBytes=10 * 1024 * 1024, backupCount=5)
    ]
)
logger = logging.getLogger(__name__)

currentVersion = '2.0'


class App(ttk.Frame):
    def __init__(self, parent):
        ttk.Frame.__init__(self)
        self.root = parent

        for index in [0, 1, 2]:
            self.columnconfigure(index=index, weight=1)
            self.rowconfigure(index=index, weight=1)

        # 右键菜单
        self.entry_context_menu = Menu(self, tearoff=0)
        self.entry_context_menu.add_command(label="剪切", command=self.cut_text)
        self.entry_context_menu.add_command(label="复制", command=self.copy_text)
        self.entry_context_menu.add_command(label="粘贴", command=self.paste_text)
        self.entry_context_menu.add_separator()
        self.entry_context_menu.add_command(label="全选", command=self.select_all)

        # 进度/状态
        self.progress_lock = threading.Lock()
        self.download_progress = {}  # thread_id -> text
        self.stop_flags = {}         # thread_id -> threading.Event()
        self.outtmpl_map = {}        # thread_id -> outtmpl
        self.active_downloads = set()
        self.download_speed = {}     # thread_id -> speed (bytes/sec)
        self.remaining_time = {}     # thread_id -> remaining time (seconds)

        self.has_activity = False

        # 并行下载限制
        self.max_workers = 3
        self.semaphore = threading.Semaphore(self.max_workers)

        # 统计
        self.total_jobs = 0
        self.done_jobs = 0

        # UI节流
        self._last_ui_update = 0.0
        self._ui_update_interval = 0.15
        self._pending_ui_update = False

        # 确定下载目录（打包后使用 exe 所在目录）
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后：使用 exe 所在目录
            self.download_dir = os.path.join(os.path.dirname(sys.executable), 'downloads')
        else:
            # 开发模式：使用脚本所在目录
            self.download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')

        self.setup_widgets()

    # ------------------ 工具函数 ------------------

    def sanitize_filename(self, name: str, max_len=120) -> str:
        """Windows安全文件名"""
        name = (name or "").strip()
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', name)
        name = re.sub(r"\s+", " ", name).strip()
        name = name.rstrip(". ")
        if not name:
            name = "video"
        return name[:max_len]

    def cleanup_temp_files(self, thread_id: str):
        """
        删除取消后的yt-dlp临时文件。
        """
        try:
            with self.progress_lock:
                outtmpl = self.outtmpl_map.get(thread_id)

            if not outtmpl:
                return

            # outtmpl可能是:
            # downloads/name.%(ext)s
            # downloads/name/video.%(ext)s
            base = outtmpl.replace("%(ext)s", "*")

            folder = os.path.dirname(base)
            if not os.path.isdir(folder):
                return

            # "video.*" -> "video."
            prefix = os.path.basename(base).replace("*", "")

            for fn in os.listdir(folder):
                full = os.path.join(folder, fn)

                # 只清理"自己的"文件（按前缀）
                if prefix and not fn.startswith(prefix):
                    continue

                # yt-dlp的典型临时文件后缀
                if fn.endswith((".part", ".ytdl", ".tmp")) or ".part" in fn:
                    try:
                        os.remove(full)
                    except Exception:
                        pass

        except Exception as e:
            logger.warning(f"[{thread_id}] cleanup_temp_files error: {e}")

    # ------------------ 界面 ------------------

    def setup_widgets(self):
        self.widgets_frame = ttk.Frame(self, padding=(0, 5, 0, 0))
        self.widgets_frame.grid(row=0, column=1, padx=10, pady=(5, 0), sticky="nsew")
        self.widgets_frame.columnconfigure(index=0, weight=1)
        self.widgets_frame.rowconfigure(index=4, weight=1)  # 为表格

        self.label = ttk.Label(
            self.widgets_frame,
            text="请粘贴一个或多个视频链接（用逗号分隔）",
            justify="center",
            font=("-size", 15, "-weight", "bold"),
        )
        self.label.grid(row=0, column=0, padx=0, pady=10, sticky="n")

        self.entry_nm = ttk.Entry(self.widgets_frame, font=("Calibri 22"))
        self.entry_nm.insert(tk.END, str(''))
        self.entry_nm.grid(
            row=1, column=0, columnspan=10,
            padx=(5, 5), ipadx=150, ipady=5,
            pady=(0, 0), sticky="ew"
        )
        self.entry_nm.bind('<Return>', self.on_enter_pressed)

        # 快捷键
        self.entry_nm.bind('<Control-c>', self.copy_text)
        self.entry_nm.bind('<Control-v>', self.paste_text)
        self.entry_nm.bind('<Control-x>', self.cut_text)
        self.entry_nm.bind('<Control-a>', self.select_all)
        self.entry_nm.bind('<Control-KeyPress>', self.handle_control_key)
        self.entry_nm.bind("<Button-3>", self.show_context_menu)

        # 下载按钮
        self.bt_frame = ttk.Frame(self, padding=(0, 0, 0, 0))
        self.bt_frame.grid(row=2, column=0, padx=10, pady=(5, 0), columnspan=10, sticky="n")

        self.accentbutton = ttk.Button(
            self.bt_frame,
            text="下载视频",
            style="Accent.TButton",
            command=self.get_directory_string
        )
        self.accentbutton.grid(row=0, column=0, columnspan=3, ipadx=30, padx=2, pady=(5, 0), sticky="n")
        self.bt_frame.columnconfigure(0, weight=1)

        # --- 选项 ---
        self.check_frame = ttk.Frame(self.widgets_frame)
        self.check_frame.grid(row=3, column=0, padx=20, pady=(5, 5), sticky="w")

        self.var_random_name = tk.StringVar(value='')
        self.var_limit_length = tk.StringVar(value='')
        self.var_folder = tk.StringVar(value='')

        self.check_random = ttk.Checkbutton(
            self.check_frame,
            text="随机文件名",
            variable=self.var_random_name,
            onvalue='random',
            offvalue='',
            style="Switch.TCheckbutton"
        )
        self.check_random.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="w")

        self.check_limit = ttk.Checkbutton(
            self.check_frame,
            text="文件名最长 50 个字符",
            variable=self.var_limit_length,
            onvalue='limit',
            offvalue='',
            style="Switch.TCheckbutton"
        )
        self.check_limit.grid(row=0, column=1, padx=(0, 10), pady=0, sticky="w")

        self.check_folder = ttk.Checkbutton(
            self.check_frame,
            text="为每个视频创建单独文件夹",
            variable=self.var_folder,
            onvalue='folder',
            offvalue='',
            style="Switch.TCheckbutton"
        )
        self.check_folder.grid(row=0, column=2, padx=0, pady=0, sticky="w")

        # --- 代理设置 ---
        self.proxy_frame = ttk.Frame(self.widgets_frame)
        self.proxy_frame.grid(row=3, column=0, padx=20, pady=(5, 5), sticky="ew")
        self.proxy_frame.columnconfigure(1, weight=1)

        self.proxy_label = ttk.Label(
            self.proxy_frame,
            text="代理:",
            font=("-size", 10)
        )
        self.proxy_label.grid(row=0, column=0, padx=(0, 5), pady=0, sticky="w")

        # 预设代理选项
        self.proxy_presets = [
            "不使用代理",
            "http://127.0.0.1:7890",
            "http://127.0.0.1:10808",
            "socks5://127.0.0.1:1080",
            "自定义..."
        ]
        
        self.proxy_var = tk.StringVar(value="不使用代理")
        self.proxy_combo = ttk.Combobox(
            self.proxy_frame,
            textvariable=self.proxy_var,
            values=self.proxy_presets,
            font=("Calibri", 11),
            width=35,
            state="readonly"
        )
        self.proxy_combo.grid(row=0, column=1, padx=0, pady=0, sticky="ew")
        self.proxy_combo.bind("<<ComboboxSelected>>", self.on_proxy_selected)

        # 自定义代理输入框（默认隐藏）
        self.proxy_entry = ttk.Entry(
            self.proxy_frame,
            font=("Calibri", 11),
            width=35
        )
        self.proxy_entry.insert(0, '')
        self.proxy_entry.bind('<Return>', self.confirm_custom_proxy)
        self.proxy_entry.bind('<FocusOut>', self.confirm_custom_proxy)
        
        # 存储当前使用的代理值
        self.current_proxy = ""

        # --- 下载列表 ---
        self.table_frame = ttk.Frame(self.widgets_frame)
        self.table_frame.grid(row=4, column=0, padx=10, pady=(5, 0), sticky="nsew")
        self.table_frame.rowconfigure(0, weight=1)
        self.table_frame.columnconfigure(0, weight=1)

        # 列表背景样式
        style = ttk.Style()
        style.configure(
            "Custom.Treeview",
            background="#EDEEF0",
            fieldbackground="#EDEEF0",
            foreground="#000000",
            rowheight=24
        )
        style.map("Custom.Treeview", background=[("selected", "#D6D8DC")])

        columns = ("id", "name", "size", "speed", "status", "action")
        self.tree = ttk.Treeview(
            self.table_frame,
            columns=columns,
            show="headings",
            height=5,
            style="Custom.Treeview"
        )

        self.tree.heading("id", text="序号")
        self.tree.heading("name", text="名称 / 来源")
        self.tree.heading("size", text="大小")
        self.tree.heading("speed", text="速度 / 剩余时间")
        self.tree.heading("status", text="状态")
        self.tree.heading("action", text="")

        self.tree.column("id", width=50, anchor="center", stretch=False)
        self.tree.column("name", width=280, anchor="w", stretch=True)
        self.tree.column("size", width=80, anchor="w", stretch=False)
        self.tree.column("speed", width=150, anchor="w", stretch=False)
        self.tree.column("status", width=120, anchor="w", stretch=False)
        self.tree.column("action", width=40, anchor="center", stretch=False)

        self.tree.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        # 表格点击（取消）
        self.tree.bind("<Button-1>", self.on_tree_click)

        # --- 底部状态栏 ---
        self.status_label = ttk.Label(
            self.widgets_frame,
            text="准备就绪",
            justify="left",
            anchor="w",
            font=("-size", 10, "-weight", "normal"),
            wraplength=650,
            foreground="#aaaaaa"
        )
        self.status_label.grid(row=5, column=0, padx=20, pady=(5, 5), sticky="ew")

        # --- 底部按钮 ---
        self.copy_frame = ttk.Frame(self, padding=(0, 0, 0, 10))
        self.copy_frame.grid(row=8, column=0, padx=10, pady=5, columnspan=10, sticky="s")

        self.UrlButton = ttk.Button(self.copy_frame, text="关于", style="Url.TButton", command=self.openweb)
        self.UrlButton.grid(row=1, column=0, padx=20, pady=0, columnspan=2, sticky="n")

        self.UrlButton = ttk.Button(
            self.copy_frame, text="版本: " + currentVersion + " ", style="Url.TButton", command=self.checkUpdate
        )
        self.UrlButton.grid(row=1, column=4, padx=20, pady=0, columnspan=2, sticky="w")

        self.UrlButton = ttk.Button(self.copy_frame, text="捐赠", style="Url.TButton", command=self.donate)
        self.UrlButton.grid(row=1, column=7, padx=20, pady=0, columnspan=2, sticky="w")

    def on_tree_click(self, event):
        """点击Action列取消特定下载"""
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)

        if not row_id:
            return

        # Action列 = 6
        if col != "#6":
            return

        values = self.tree.item(row_id, "values")
        if not values:
            return

        thread_id = values[0]  # "#1"
        self.cancel_download(thread_id)

    def cancel_download(self, thread_id: str):
        with self.progress_lock:
            flag = self.stop_flags.get(thread_id)

        if flag:
            flag.set()
            self.set_status_error(f"正在停止 {thread_id} ...")

    # ------------------ 通用处理函数 ------------------

    def show_context_menu(self, event):
        try:
            self.entry_nm.focus_set()
            self.entry_context_menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            logger.error(f"Error showing context menu: {str(e)}")
            self.set_status_error(f"错误: {str(e)}")
        finally:
            self.entry_context_menu.grab_release()

    def handle_control_key(self, event):
        try:
            keycode = event.keycode
            if keycode == 67:
                self.copy_text(); return "break"
            elif keycode == 86:
                self.paste_text(); return "break"
            elif keycode == 88:
                self.cut_text(); return "break"
            elif keycode == 65:
                self.select_all(); return "break"
        except Exception as e:
            self.set_status_error(f"错误: {str(e)}")
            return "break"

    def copy_text(self, event=None):
        self.entry_nm.event_generate("<<Copy>>")
        return "break"

    def paste_text(self, event=None):
        self.entry_nm.event_generate("<<Paste>>")
        return "break"

    def cut_text(self, event=None):
        self.entry_nm.event_generate("<<Cut>>")
        return "break"

    def select_all(self, event=None):
        self.entry_nm.event_generate("<<SelectAll>>")
        return "break"

    def openweb(self):
        webbrowser.open_new_tab('https://github.com/blyamur/VK-Video-Download')

    def donate(self):
        webbrowser.open_new_tab('https://ko-fi.com/monseg')

    def checkUpdate(self, method='Button'):
        try:
            logger.info("检查更新")
            github_page = requests.get('https://raw.githubusercontent.com/blyamur/VK-Video-Download/main/README.md')
            github_page_html = str(github_page.content).split()
            version = None
            for i in range(0, 9):
                try:
                    idx = github_page_html.index(f'1.{i}')
                    version = github_page_html[idx]
                    break
                except ValueError:
                    continue

            if version and float(version) > float(currentVersion):
                update = messagebox.askyesno("更新", f"发现新版本 {version}，是否打开下载页面？")
                if update:
                    webbrowser.open_new_tab('https://github.com/blyamur/VK-Video-Download')
            elif method == 'Button':
                messagebox.showinfo("更新", "当前已是最新版本")
        except Exception as e:
            logger.error(f"检查更新出错: {e}")

    # ------------------ 下载 ------------------

    def get_directory_string(self):
        try:
            urls_input = self.entry_nm.get().strip()
            if not urls_input:
                self.set_status_error("请输入链接")
                return

            video_urls = [url.strip() for url in urls_input.split(',') if url.strip()]
            if not video_urls:
                self.set_status_error("没有有效的链接")
                return

            video_urls = list(dict.fromkeys(video_urls))
            
            # 找到当前最大的序号，从下一个开始
            existing_items = self.tree.get_children()
            max_idx = 0
            for item_id in existing_items:
                try:
                    # 从 thread_id 中提取序号，例如 "#5" -> 5
                    idx_str = item_id.replace("#", "")
                    idx = int(idx_str)
                    if idx > max_idx:
                        max_idx = idx
                except (ValueError, AttributeError):
                    continue
            
            start_idx = max_idx + 1
            new_jobs_count = len(video_urls)
            
            # 更新总任务数（累加）
            self.total_jobs += new_jobs_count
            # done_jobs 保持不变，因为新任务还没完成

            self.has_activity = True
            self.entry_nm.delete(0, tk.END)

            # 不清空表格，直接追加新行
            # 填充行
            for idx, url in enumerate(video_urls, start=start_idx):
                thread_id = f"#{idx}"

                short_url = url
                if len(short_url) > 60:
                    short_url = short_url[:57] + "..."

                with self.progress_lock:
                    self.stop_flags[thread_id] = threading.Event()
                    self.download_progress[thread_id] = "  0.0%"

                self.tree.insert("", "end", iid=thread_id, values=(thread_id, short_url, "-", "-", "  0.0%", "✖"))


                t = threading.Thread(target=self.download_video, args=(url, idx))
                t.daemon = True
                t.start()

            self.update_status_bar(force=True)

        except Exception as e:
            logger.error(f"错误: {e}")
            self.set_status_error(f"错误: {str(e)}")

    def make_progress_hook(self, thread_id):
        tid = thread_id

        def hook(d):
            self.my_hook(d, tid)

        return hook

    def download_video(self, video_url, serial_number):
        thread_id = f"#{serial_number}"

        with self.semaphore:
            try:
                os.makedirs(self.download_dir, exist_ok=True)

                with self.progress_lock:
                    self.active_downloads.add(thread_id)

                # 名称
                timestr = datetime.datetime.now().strftime('%d%m%Y_%H%M%S_%f')[:-4]
                random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=4))

                use_random_name = (self.var_random_name.get() == 'random')
                limit_length = (self.var_limit_length.get() == 'limit')
                use_folder = (self.var_folder.get() == 'folder')

                title = "video"
                file_size = None

                if not use_random_name:
                    try:
                        proxy_url = self.get_proxy_url()
                        ydl_info_opts = {'quiet': True, 'no_warnings': True}
                        if proxy_url:
                            ydl_info_opts['proxy'] = proxy_url
                        
                        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
                            info = ydl.extract_info(video_url, download=False) or {}
                            title = (info.get('title') or "video")
                            
                            # 获取文件大小（如果可用）
                            filesize = info.get('filesize') or info.get('filesize_approx')
                            if filesize:
                                file_size = filesize
                    except Exception as e:
                        logger.warning(f"[{serial_number}] 无法获取视频信息: {e}")

                title = self.sanitize_filename(title, max_len=50 if limit_length else 120)

                if use_random_name:
                    filename_base = f"{timestr}_{random_suffix}"
                    display_name = filename_base
                else:
                    filename_base = f"{title}_{timestr}_{random_suffix}"
                    display_name = title

                display_name = self.sanitize_filename(display_name, max_len=80)

                # 格式化文件大小
                def format_size(size_bytes):
                    if size_bytes is None:
                        return "-"
                    try:
                        size_bytes = int(size_bytes)
                        for unit in ['B', 'KB', 'MB', 'GB']:
                            if size_bytes < 1024.0:
                                return f"{size_bytes:.1f} {unit}"
                            size_bytes /= 1024.0
                        return f"{size_bytes:.1f} TB"
                    except:
                        return "-"

                # 更新视频信息（名称、大小）
                def _set_info():
                    if self.tree.exists(thread_id):
                        values = list(self.tree.item(thread_id, "values"))
                        # 清理可能导致显示问题的字符（换行符、制表符等）
                        clean_name = display_name.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                        # 限制显示名称长度，避免表格显示问题
                        max_display_len = 35
                        if len(clean_name) > max_display_len:
                            display_name_truncated = clean_name[:max_display_len] + "..."
                        else:
                            display_name_truncated = clean_name
                        values[1] = display_name_truncated
                        values[2] = format_size(file_size)  # 大小
                        values[3] = "-"  # 速度/剩余时间初始值
                        self.tree.item(thread_id, values=values)
                        # 强制刷新表格显示
                        self.tree.update_idletasks()

                self.root.after(0, _set_info)

                # 不强制扩展名
                if use_folder:
                    outtmpl = os.path.join(self.download_dir, f'{filename_base}/video.%(ext)s')
                else:
                    outtmpl = os.path.join(self.download_dir, f'{filename_base}.%(ext)s')

                with self.progress_lock:
                    self.outtmpl_map[thread_id] = outtmpl

                # 获取代理设置
                proxy_url = self.get_proxy_url()
                
                ydl_opts = {
                    'outtmpl': outtmpl,
                    'quiet': True,
                    'no_warnings': True,
                    'logtostderr': False,
                    'progress_hooks': [self.make_progress_hook(thread_id)],
                }
                
                # 如果设置了代理，添加到配置中
                if proxy_url:
                    ydl_opts['proxy'] = proxy_url
                    logger.info(f"[{serial_number}] 使用代理: {proxy_url}")

                # 重要：在这里捕获取消，以真正停止 ydl.download()
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
                except yt_dlp.utils.DownloadError as e:
                    if "Cancelled by user" in str(e):
                        with self.progress_lock:
                            self.download_progress[thread_id] = "已取消 ⛔"
                            self.download_speed[thread_id] = "-"
                            self.remaining_time[thread_id] = "-"
                        self.update_row(thread_id, "已取消 ⛔", speed="-", eta="-")
                        self.cleanup_temp_files(thread_id)
                        self.update_status_bar(force=True)
                        return
                    raise

                self.update_status_bar(force=True)

            except Exception as e:
                logger.error(f"[{serial_number}] 下载出错: {e}")
                with self.progress_lock:
                    self.download_progress[thread_id] = "错误 ❌"
                    self.download_speed[thread_id] = "-"
                    self.remaining_time[thread_id] = "-"
                self.update_row(thread_id, "错误 ❌", speed="-", eta="-")
                self.update_status_bar(force=True)

            finally:
                with self.progress_lock:
                    self.active_downloads.discard(thread_id)

    def my_hook(self, d, thread_id):
        stop_event = self.stop_flags.get(thread_id)
        if stop_event and stop_event.is_set():
            # 重要：不在这里捕获，让它中断 ydl.download()
            raise yt_dlp.utils.DownloadError("Cancelled by user")

        if d.get('status') == 'downloading':
            raw_percent = d.get('_percent_str', '0%')
            clean_percent = re.sub(r'\x1b\[[0-9;]*m', '', raw_percent)
            clean_percent = clean_percent.replace(',', '.').strip()

            match = re.search(r'(\d+\.?\d*)\s*%', clean_percent)
            val = float(match.group(1)) if match else 0.0
            percent = f"{val:5.1f}%"

            with self.progress_lock:
                self.download_progress[thread_id] = percent

            # 更新文件大小（如果可用）
            downloaded_bytes = d.get('downloaded_bytes')
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
            
            # 获取下载速度并计算剩余时间
            speed = d.get('speed')  # bytes per second
            speed_str = "-"
            eta_str = "-"
            
            if speed and speed > 0:
                # 格式化速度
                speed_kb = speed / 1024.0
                if speed_kb < 1024:
                    speed_str = f"{speed_kb:.1f} KB/s"
                else:
                    speed_mb = speed_kb / 1024.0
                    speed_str = f"{speed_mb:.1f} MB/s"
                
                # 计算剩余时间
                if total_bytes and downloaded_bytes:
                    remaining_bytes = total_bytes - downloaded_bytes
                    if remaining_bytes > 0:
                        eta_seconds = remaining_bytes / speed
                        if eta_seconds < 60:
                            eta_str = f"{int(eta_seconds)}秒"
                        elif eta_seconds < 3600:
                            eta_str = f"{int(eta_seconds // 60)}分{int(eta_seconds % 60)}秒"
                        else:
                            hours = int(eta_seconds // 3600)
                            minutes = int((eta_seconds % 3600) // 60)
                            eta_str = f"{hours}时{minutes}分"
            
            with self.progress_lock:
                self.download_speed[thread_id] = speed_str
                self.remaining_time[thread_id] = eta_str
            
            self.update_row(thread_id, percent, downloaded_bytes=downloaded_bytes, total_bytes=total_bytes, speed=speed_str, eta=eta_str)
            self.update_status_bar()

        elif d.get('status') == 'finished':
            # 下载完成，获取最终文件大小
            info_dict = d.get('info_dict', {})
            final_size = info_dict.get('filesize') or info_dict.get('filesize_approx')
            
            # 如果info_dict中没有大小信息，尝试从文件系统读取
            if not final_size:
                try:
                    filename = d.get('filename')
                    if filename and os.path.exists(filename):
                        final_size = os.path.getsize(filename)
                except Exception as e:
                    logger.warning(f"[{thread_id}] 无法获取文件大小: {e}")
            
            with self.progress_lock:
                self.download_progress[thread_id] = "完成 ✅"
                self.download_speed[thread_id] = "-"
                self.remaining_time[thread_id] = "-"
            self.update_row(thread_id, "完成 ✅", final_size=final_size, speed="-", eta="-")
            self.update_status_bar(force=True)

    # ------------------ UI updates ------------------

    def update_row(self, thread_id: str, status_text: str, downloaded_bytes=None, total_bytes=None, final_size=None, speed=None, eta=None):
        def _update():
            if self.tree.exists(thread_id):
                values = list(self.tree.item(thread_id, "values"))
                # values = (#, name, size, speed, status, X)
                
                # 更新状态
                values[4] = status_text
                
                # 更新文件大小
                def format_size(size_bytes):
                    if size_bytes is None:
                        return values[2] if len(values) > 2 else "-"  # 保持原值
                    try:
                        size_bytes = int(size_bytes)
                        for unit in ['B', 'KB', 'MB', 'GB']:
                            if size_bytes < 1024.0:
                                return f"{size_bytes:.1f} {unit}"
                            size_bytes /= 1024.0
                        return f"{size_bytes:.1f} TB"
                    except:
                        return values[2] if len(values) > 2 else "-"
                
                if final_size is not None:
                    # 下载完成，显示最终大小
                    values[2] = format_size(final_size)
                elif total_bytes:
                    # 显示总大小
                    values[2] = format_size(total_bytes)
                elif downloaded_bytes:
                    # 显示已下载大小（如果总大小未知）
                    values[2] = format_size(downloaded_bytes)
                
                # 更新速度和剩余时间
                if speed is not None and eta is not None:
                    if eta != "-":
                        values[3] = f"{speed} / {eta}"
                    else:
                        values[3] = speed
                elif speed is not None:
                    values[3] = speed
                elif len(values) > 3 and values[3] == "":
                    values[3] = "-"

                # 如果完成/错误/取消 - 移除X
                if status_text in ("完成 ✅", "错误 ❌", "已取消 ⛔"):
                    values[5] = ""
                    # 清除速度和剩余时间
                    values[3] = "-"

                self.tree.item(thread_id, values=values)

        self.root.after(0, _update)

    def update_status_bar(self, force=False):
        now = time.time()

        if not force:
            if (now - self._last_ui_update) < self._ui_update_interval:
                if not self._pending_ui_update:
                    self._pending_ui_update = True
                    self.root.after(int(self._ui_update_interval * 1000), self.update_status_bar)
                return

        self._pending_ui_update = False
        self._last_ui_update = now

        with self.progress_lock:
            done = sum(1 for v in self.download_progress.values() if v in ("完成 ✅", "错误 ❌", "已取消 ⛔"))
            total = self.total_jobs if self.total_jobs else len(self.download_progress)

        text = f"完成: {done}/{total}"
        self.root.after(0, lambda: self.status_label.configure(text=text, foreground="#000000"))

    def set_status_error(self, msg):
        self.root.after(0, lambda: self.status_label.configure(text=msg, foreground="#d93025"))

    def on_enter_pressed(self, event):
        self.get_directory_string()

    def on_proxy_selected(self, event=None):
        """处理代理选择"""
        selected = self.proxy_var.get()
        
        if selected == "自定义...":
            # 显示输入框
            self.proxy_entry.grid(row=0, column=1, padx=0, pady=0, sticky="ew")
            self.proxy_combo.grid_remove()
            self.proxy_entry.focus()
            # 如果有之前保存的自定义代理，显示它
            if self.current_proxy and self.current_proxy not in self.proxy_presets:
                self.proxy_entry.delete(0, tk.END)
                self.proxy_entry.insert(0, self.current_proxy)
        elif selected == "不使用代理":
            # 隐藏输入框，显示下拉框
            self.proxy_entry.grid_remove()
            self.proxy_combo.grid(row=0, column=1, padx=0, pady=0, sticky="ew")
            self.current_proxy = ""
        else:
            # 使用预设代理
            self.proxy_entry.grid_remove()
            self.proxy_combo.grid(row=0, column=1, padx=0, pady=0, sticky="ew")
            self.current_proxy = selected

    def confirm_custom_proxy(self, event=None):
        """确认自定义代理输入"""
        custom_proxy = self.proxy_entry.get().strip()
        if custom_proxy:
            self.current_proxy = custom_proxy
            # 切换回下拉框，但保持自定义值
            self.proxy_entry.grid_remove()
            self.proxy_combo.grid(row=0, column=1, padx=0, pady=0, sticky="ew")
            # 更新下拉框的值显示为自定义代理（如果不在预设列表中）
            if custom_proxy not in self.proxy_presets:
                # 临时添加自定义代理到下拉框显示
                temp_values = [v for v in self.proxy_presets if v != "自定义..."]
                temp_values.append(custom_proxy)
                temp_values.append("自定义...")
                self.proxy_combo['values'] = temp_values
                self.proxy_var.set(custom_proxy)
            else:
                self.proxy_var.set(custom_proxy)

    def get_proxy_url(self):
        """获取当前选择的代理URL"""
        selected = self.proxy_var.get()
        
        if selected == "自定义...":
            # 从输入框获取
            custom = self.proxy_entry.get().strip()
            return custom if custom else self.current_proxy
        elif selected == "不使用代理":
            return ""
        else:
            # 使用预设代理或自定义代理
            return selected


if __name__ == "__main__":
    try:
        # 确定操作系统
        system = platform.system()
        is_linux = system == 'Linux'
        
        if is_linux:
            logger.info("在 Linux 上启动应用")
        
        # 确定脚本目录的绝对路径
        # 支持 PyInstaller 打包后的路径
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后的情况
            if hasattr(sys, '_MEIPASS'):
                # 单文件模式：资源在临时目录
                base_path = sys._MEIPASS
            else:
                # 目录模式：资源在 exe 所在目录
                base_path = os.path.dirname(sys.executable)
        else:
            # 开发模式：使用脚本所在目录
            base_path = os.path.dirname(os.path.abspath(__file__))
        
        script_dir = base_path
        theme_dir = os.path.join(script_dir, 'theme')
        theme_file = os.path.join(theme_dir, 'vk_theme.tcl')
        icon_ico = os.path.join(theme_dir, 'icon.ico')
        icon_png = os.path.join(script_dir, 'icon.png')
        
        if not os.path.exists(theme_file):
            raise FileNotFoundError(f"主题文件未找到: {theme_file}")

        root = tk.Tk()
        w = root.winfo_screenwidth() // 2 - 200
        h = root.winfo_screenheight() // 2 - 200
        root.geometry(f'800x430+{w}+{h}')
        root.resizable(False, False)
        root.title("VK 视频下载器")
        
        # 尝试加载图标（支持不同平台）
        icon_loaded = False
        # 首先尝试 .ico (Windows)
        if os.path.exists(icon_ico):
            try:
                root.iconbitmap(icon_ico)
                icon_loaded = True
                if is_linux:
                    logger.info("图标已从 .ico 文件加载")
            except Exception as e:
                if is_linux:
                    logger.warning(f"无法加载 .ico 图标（Linux 不直接支持 .ico）：{e}")
                else:
                    logger.warning(f"无法加载 .ico 图标：{e}")
        
        # 如果 .ico 不工作，尝试 PNG (Linux)
        if not icon_loaded and os.path.exists(icon_png):
            try:
                icon_image = tk.PhotoImage(file=icon_png)
                root.iconphoto(True, icon_image)
                # 保存引用，防止图像被垃圾回收器删除
                root.icon_image = icon_image
                icon_loaded = True
                if is_linux:
                    logger.info("图标已从 .png 文件加载 (Linux)")
            except Exception as e:
                logger.warning(f"无法加载 .png 图标：{e}")
        
        if not icon_loaded:
            logger.warning("图标未加载，应用将继续运行但无图标")
        
        # 尝试加载主题，如果失败则继续使用默认主题
        try:
            root.tk.call("source", theme_file)
            root.tk.call("set_theme", "light")
            logger.info("主题加载成功")
        except Exception as e:
            logger.warning(f"主题加载失败，使用默认主题: {e}")

        app = App(root)
        app.pack(fill="both", expand=True)
        
        # 确保窗口可见并置于最前
        root.deiconify()
        root.lift()
        root.focus_force()
        root.update()
        
        logger.info("应用已启动")

        def on_closing():
            root.destroy()
            sys.exit(0)

        root.protocol("WM_DELETE_WINDOW", on_closing)

        root.mainloop()

    except Exception as e:
        logger.error(f"启动出错: {e}")
        messagebox.showerror("错误", f"启动出错: {e}")
        sys.exit(1)
