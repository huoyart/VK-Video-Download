"""中文 GUI；所有 Tk 调用仅发生在主线程。"""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from download_core import DownloadManager, DownloadOptions, TERMINAL
from download_utils import format_size, sanitize_filename, validate_proxy

currentVersion = '2.2'
PROJECT_URL = 'https://github.com/huoyart/VK-Video-Download'
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', APP_DIR))
STATUS = {'queued': '排队中', 'extracting': '解析视频信息', 'downloading': '下载中',
          'processing': '合并 / 后处理中', 'pausing': '正在暂停…', 'paused': '已暂停',
          'retrying': '自动重试中', 'cancelling': '正在取消…', 'completed': '完成 ✅',
          'failed': '错误 ❌', 'cancelled': '已取消 ⛔'}
CANCELLABLE = frozenset({'queued', 'extracting', 'downloading', 'processing', 'pausing', 'paused', 'retrying'})


def configure_ui_style(root):
    """使用系统原生控件，避免第三方主题把空白表格绘成蓝色。"""
    style = ttk.Style(root)
    if 'vista' in style.theme_names():
        style.theme_use('vista')
    style.configure('Download.Treeview', background='#ffffff', fieldbackground='#ffffff',
                    foreground='#202124', rowheight=28, borderwidth=1)
    style.map('Download.Treeview', background=[('selected', '#e4e8ec')],
              foreground=[('selected', '#202124')])
    style.configure('Download.Treeview.Heading', foreground='#202124')
    style.configure('Download.TCombobox', selectbackground='#e4e8ec',
                    selectforeground='#202124')
    return style


def action_for_status(status):
    if status == 'failed':
        return '重试'
    if status == 'paused':
        return '继续'
    if status in ('queued', 'extracting', 'downloading'):
        return '暂停'
    return ''


def speed_text(job):
    if job.status != 'downloading':
        return '-'
    speed = f'{format_size(job.speed)}/s' if job.speed is not None and job.speed >= 0 else '-'
    if job.eta is not None and job.eta >= 0:
        seconds = int(job.eta)
        remaining = f'{seconds // 3600:d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60:02d}:{seconds % 60:02d}'
        speed += f' / {remaining}'
    return speed


class App(ttk.Frame):
    sanitize_filename = staticmethod(sanitize_filename)

    def __init__(self, parent, manager=None):
        configure_ui_style(parent)
        super().__init__(parent, padding=16)
        self.root = parent
        self.download_dir = str(APP_DIR / 'downloads')
        self._closing = False
        self._destroyed = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.setup_widgets()
        self.manager = manager or DownloadManager(max_workers=3)
        self._poll_id = self.root.after(100, self._poll_updates)
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)

    def setup_widgets(self):
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)
        ttk.Label(frame, text='VK 视频下载器', font=('Microsoft YaHei UI', 18, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(frame, text='粘贴链接，支持换行、空格或逗号分隔；Ctrl+Enter 开始').grid(row=1, column=0, sticky='w', pady=(4, 8))
        self.entry_nm = tk.Text(frame, height=3, wrap='word', font=('Microsoft YaHei UI', 11), undo=True)
        self.entry_nm.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        self.entry_nm.bind('<Control-Return>', self.on_enter_pressed)
        self.entry_nm.bind('<Control-a>', self.select_all)

        self.check_frame = ttk.Frame(frame)
        self.check_frame.grid(row=3, column=0, sticky='w', pady=4)
        self.var_random_name = tk.BooleanVar(value=False)
        self.var_limit_length = tk.BooleanVar(value=False)
        self.var_folder = tk.BooleanVar(value=False)
        for column, (label, var) in enumerate((('随机文件名', self.var_random_name),
                                               ('标题最多 50 字符', self.var_limit_length),
                                               ('每个视频独立文件夹', self.var_folder))):
            ttk.Checkbutton(self.check_frame, text=label, variable=var).grid(row=0, column=column, padx=(0, 14))
        self.split_frame = ttk.Frame(self.check_frame)
        self.split_frame.grid(row=1, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self.var_auto_split = tk.BooleanVar(value=True)
        self.auto_split_check = ttk.Checkbutton(self.split_frame, text='自动分片（目标64 MiB）',
                                               variable=self.var_auto_split)
        self.auto_split_check.grid(row=0, column=0, padx=(0, 14))
        ttk.Label(self.split_frame, text='下载线程：').grid(row=0, column=1)
        self.fragments_var = tk.StringVar(value='4')
        self.fragments_combo = ttk.Combobox(self.split_frame, textvariable=self.fragments_var,
                                            values=('1', '2', '4', '8'), state='readonly', width=3,
                                            style='Download.TCombobox')
        self.fragments_combo.grid(row=0, column=2)
        ttk.Label(self.split_frame, text='取消勾选：固定 64 MiB/片').grid(row=0, column=3, padx=(14, 0))

        self.proxy_frame = ttk.Frame(frame)
        self.proxy_frame.grid(row=4, column=0, sticky='ew', pady=(6, 12))
        self.proxy_frame.columnconfigure(1, weight=1)
        ttk.Label(self.proxy_frame, text='代理：').grid(row=0, column=0)
        self.proxy_var = tk.StringVar(value='系统代理')
        self.proxy_combo = ttk.Combobox(self.proxy_frame, textvariable=self.proxy_var, style='Download.TCombobox', values=(
            '系统代理', '不使用代理', 'http://127.0.0.1:7890', 'http://127.0.0.1:10808', 'socks5://127.0.0.1:1080'))
        self.proxy_combo.grid(row=0, column=1, sticky='ew')
        ttk.Label(self.proxy_frame, text='可输入自定义代理').grid(row=0, column=2, padx=(8, 0))

        table_frame = ttk.Frame(frame)
        table_frame.grid(row=5, column=0, sticky='nsew')
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_frame, columns=('id', 'name', 'size', 'speed', 'status', 'action'),
                                 show='headings', height=8, selectmode='none', style='Download.Treeview')
        for column, title, width in (('id', '序号', 55), ('name', '名称 / 来源', 280), ('size', '大小', 90),
                                     ('speed', '速度 / 剩余时间', 185), ('status', '状态 / 取消', 175), ('action', '操作', 80)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=width if column == 'speed' else 45,
                             stretch=column == 'name', anchor='e' if column == 'status' else 'w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<Button-1>', self.on_tree_click)
        self.tree.bind('<Double-1>', self.show_job_details)

        self.status_label = ttk.Label(frame, text='准备就绪', wraplength=820)
        self.status_label.grid(row=6, column=0, sticky='ew', pady=10)
        self.directory_label = ttk.Label(frame, text=f'保存到：{self.download_dir}', wraplength=820)
        self.directory_label.grid(row=7, column=0, sticky='ew')
        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, sticky='ew', pady=(12, 0))
        self.accentbutton = ttk.Button(buttons, text='下载视频', command=self.get_directory_string)
        self.accentbutton.pack(side='left', padx=(0, 8))
        for title, command in (('选择目录', self.choose_directory), ('打开目录', self.open_directory),
                               ('清除已结束', self.clear_finished), (f'版本 {currentVersion}', self.checkUpdate)):
            ttk.Button(buttons, text=title, command=command).pack(side='left', padx=4)

    def get_proxy_url(self):
        value = self.proxy_var.get().strip()
        if value == '系统代理':
            return None
        return validate_proxy('' if value == '不使用代理' else value)

    def get_directory_string(self):
        if self._closing:
            return
        try:
            options = DownloadOptions(directory=self.download_dir, random_name=self.var_random_name.get(),
                limit_length=self.var_limit_length.get(), use_folder=self.var_folder.get(),
                proxy=self.get_proxy_url(), fragments=int(self.fragments_var.get()),
                auto_split=self.var_auto_split.get())
            jobs = self.manager.submit(self.entry_nm.get('1.0', 'end'), options)
            if not jobs:
                self.set_status_error('这些链接已在下载或排队中')
                return
            for job in jobs:
                self.tree.insert('', 'end', iid=job.id, values=(job.id, job.url, '-', '-',
                    self._display_status(job), action_for_status(job.status)))
            self.entry_nm.delete('1.0', 'end')
            self.status_label.configure(text=f'已添加 {len(jobs)} 个任务', foreground='#16803c')
        except (ValueError, OSError) as exc:
            self.set_status_error(str(exc))

    def _poll_updates(self):
        if self._destroyed:
            return
        updates = self.manager.drain_updates()
        for job in updates:
            if not self.tree.exists(job.id):
                continue
            status = self._display_status(job)
            size = format_size(job.total if job.total is not None else job.downloaded)
            self.tree.item(job.id, values=(job.id, job.title or job.url, size,
                speed_text(job), status, action_for_status(job.status)))
        if updates:
            jobs = self.manager.snapshot()
            counts = {status: sum(job.status == status for job in jobs) for status in TERMINAL}
            active = sum(job.status not in TERMINAL and job.status not in ('queued', 'paused') for job in jobs)
            queued = sum(job.status == 'queued' for job in jobs)
            paused = sum(job.status == 'paused' for job in jobs)
            self.status_label.configure(text=f'总计 {len(jobs)} · 成功 {counts["completed"]} · 失败 {counts["failed"]} · '
                f'取消 {counts["cancelled"]} · 运行 {active} · 排队 {queued} · 暂停 {paused}', foreground='#333333')
        if self._closing:
            if self.manager.is_idle():
                self._destroy()
                return
            self.status_label.configure(text='正在取消任务并等待网络请求 / 后处理退出…')
        self._poll_id = self.root.after(100, self._poll_updates)

    @staticmethod
    def _display_status(job):
        status = STATUS[job.status]
        if job.status == 'downloading' and job.total:
            status = f'{min(100, 100 * job.downloaded / job.total):.1f}%'
        if job.status in CANCELLABLE:
            status += ' · 取消'
        return status

    def on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != 'cell':
            return
        job_id = self.tree.identify_row(event.y)
        if not job_id:
            return
        column = self.tree.identify_column(event.x)
        job = next((item for item in self.manager.snapshot() if item.id == job_id), None)
        if not job:
            return
        if column == '#6':
            action = action_for_status(job.status)
            if action == '重试':
                try:
                    started = self.manager.retry(job_id, proxy=self.get_proxy_url())
                except ValueError as exc:
                    self.set_status_error(str(exc))
                    return
                if started is False:
                    self.set_status_error(f'{job_id} 重试未启动，请查看任务详情')
            elif action == '继续':
                if not self.manager.resume(job_id):
                    self.set_status_error(f'{job_id} 继续失败，请查看任务详情')
            elif action == '暂停':
                self.manager.pause(job_id)
        elif column == '#5' and job.status in CANCELLABLE:
            x, _, width, _ = self.tree.bbox(job_id, 'status')
            if event.x >= x + width - 52 and messagebox.askyesno(
                    '取消任务', f'确定取消 {job_id}？此操作不能继续。', parent=self.root):
                self.manager.cancel(job_id)

    def show_job_details(self, event):
        job_id = self.tree.identify_row(event.y)
        for job in self.manager.snapshot():
            if job.id == job_id:
                details = [job.title or job.url, STATUS[job.status]]
                part_count = getattr(job, 'part_count', 0)
                active_workers = getattr(job, 'active_workers', 0)
                part_size = getattr(job, 'part_size', 0)
                if part_count:
                    details.append(f'实际分片数：{part_count}')
                if active_workers:
                    details.append(f'实际工作线程：{active_workers}')
                if part_size:
                    details.append(f'最大片大小：{format_size(part_size)}（{part_size} 字节）')
                if job.error:
                    details.append(job.error)
                messagebox.showinfo('任务详情', '\n'.join(details), parent=self.root)
                break

    def clear_finished(self):
        for job_id in self.manager.clear_finished():
            if self.tree.exists(job_id):
                self.tree.delete(job_id)

    def choose_directory(self):
        directory = filedialog.askdirectory(parent=self.root, initialdir=self.download_dir)
        if directory:
            self.download_dir = directory
            self.directory_label.configure(text=f'保存到：{directory}')

    def open_directory(self):
        try:
            Path(self.download_dir).mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(self.download_dir)
            else:
                webbrowser.open(Path(self.download_dir).resolve().as_uri())
        except OSError as exc:
            self.set_status_error(str(exc))

    def set_status_error(self, message):
        self.status_label.configure(text=message, foreground='#d93025')

    def on_enter_pressed(self, event=None):
        self.get_directory_string()
        return 'break'

    def select_all(self, event=None):
        self.entry_nm.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def checkUpdate(self):
        webbrowser.open(PROJECT_URL)

    def on_closing(self):
        if self._closing:
            return
        if not self.manager.is_idle() and not messagebox.askyesno('退出', '取消当前任务并退出？', parent=self.root):
            return
        self._closing = True
        self.accentbutton.state(['disabled'])
        self.manager.shutdown()
        if self.manager.is_idle():
            self._destroy()

    def _destroy(self):
        self._destroyed = True
        self.root.after_cancel(self._poll_id)
        self.root.destroy()


def main():
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(RotatingFileHandler(APP_DIR / 'vk_video_download.log', maxBytes=2 * 1024 * 1024,
                                           backupCount=3, encoding='utf-8'))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=handlers)
    root = tk.Tk()
    root.title('VK 视频下载器')
    root.geometry('940x620')
    root.minsize(880, 560)
    try:
        icon = RESOURCE_DIR / 'theme' / 'icon.ico'
        if icon.is_file() and sys.platform == 'win32':
            root.iconbitmap(str(icon))
    except tk.TclError:
        logging.warning('使用系统默认主题 / 图标')
    app = App(root)
    app.pack(fill='both', expand=True)
    root.mainloop()


if __name__ == '__main__':
    main()
