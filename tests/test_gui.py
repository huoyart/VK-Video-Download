"""GUI 回归测试：用内存任务代替真实网络下载。"""
import gc
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from vk_video_download import App, action_for_status, currentVersion, speed_text


def job(status, **changes):
    fields = dict(id='#1', url='https://example.com/video', title='测试视频',
                  status=status, downloaded=0, total=None, speed=None, eta=None, error='')
    fields.update(changes)
    return SimpleNamespace(**fields)


class FakeManager:
    def __init__(self):
        self.jobs = []
        self.updates = []
        self.calls = []

    def snapshot(self):
        return self.jobs[:]

    def submit(self, text, options):
        self.calls.append(('submit', text, options))
        item = job('queued', id=f'#{len(self.jobs) + 1}')
        self.jobs.append(item)
        return [item]

    def drain_updates(self):
        result, self.updates = self.updates, []
        return result

    def retry(self, job_id, *, proxy=None):
        self.calls.append(('retry', job_id, proxy))
        return True

    def resume(self, job_id):
        self.calls.append(('resume', job_id))
        return True

    def pause(self, job_id):
        self.calls.append(('pause', job_id))
        return True

    def cancel(self, job_id):
        self.calls.append(('cancel', job_id))
        return True

    def is_idle(self):
        return True

    def shutdown(self):
        pass


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'无图形窗口：{exc}')
        self.root.geometry('1000x650')
        self.manager = FakeManager()
        self.app = App(self.root, self.manager)
        self.app.pack(fill='both', expand=True)
        self.root.update()
        self.root.after_cancel(self.app._poll_id)
        self.app._poll_id = None

    def tearDown(self):
        try:
            if hasattr(self, 'root'):
                if hasattr(self, 'app'):
                    self.app._destroyed = True
                    if self.app._poll_id is not None:
                        self.root.after_cancel(self.app._poll_id)
                        self.app._poll_id = None
                self.root.destroy()
        finally:
            # Tk 变量和控件存在循环引用；避免后续下载线程触发其析构。
            self.__dict__.pop('app', None)
            self.__dict__.pop('manager', None)
            self.__dict__.pop('root', None)
            gc.collect()  # unittest 在主线程执行 tearDown 和 Tk 析构。

    def pump(self):
        if self.app._poll_id is not None:
            self.root.after_cancel(self.app._poll_id)
        self.app._poll_updates()
        self.root.after_cancel(self.app._poll_id)
        self.app._poll_id = None
        self.root.update()

    def show_job(self, item):
        self.manager.jobs = [item]
        self.manager.updates = [item]
        self.app.tree.insert('', 'end', iid=item.id, values=(item.id, item.url, '-', '-', '-', '-'))
        self.pump()

    def click(self, column, fraction=0.5):
        x, y, width, height = self.app.tree.bbox('#1', column)
        self.assertGreater(width, 0)
        self.app.on_tree_click(SimpleNamespace(x=x + int(width * fraction), y=y + height // 2))

    def test_neutral_style_and_readable_speed_column(self):
        style = ttk.Style(self.root)
        if 'vista' in style.theme_names():
            self.assertEqual(style.theme_use(), 'vista')
        self.assertEqual(style.lookup('Download.Treeview', 'fieldbackground'), '#ffffff')
        self.assertEqual(style.map('Download.Treeview', 'background')[0], ('selected', '#e4e8ec'))
        self.assertEqual(str(self.app.tree.cget('selectmode')), 'none')
        self.assertEqual(self.app.accentbutton.cget('style'), '')
        self.assertGreaterEqual(int(self.app.tree.column('speed', 'width')), 185)

    def test_auto_split_defaults_and_thread_choices(self):
        self.assertEqual(currentVersion, '2.2')
        self.assertTrue(self.app.var_auto_split.get())
        self.assertIn('selected', self.app.auto_split_check.state())
        self.assertEqual(self.app.auto_split_check.cget('text'), '自动分片（目标64 MiB）')
        self.assertEqual(self.app.fragments_var.get(), '4')
        self.assertEqual(tuple(self.app.fragments_combo.cget('values')), ('1', '2', '4', '8'))
        self.assertEqual(str(self.app.fragments_combo.cget('state')), 'readonly')
        self.app.auto_split_check.invoke()
        self.assertFalse(self.app.var_auto_split.get())
        self.app.auto_split_check.invoke()
        self.assertTrue(self.app.var_auto_split.get())

    def test_split_controls_fit_default_and_minimum_window(self):
        self.assertEqual(int(self.app.split_frame.grid_info()['row']), 1)
        for geometry in ('940x620', '880x560'):
            with self.subTest(geometry=geometry):
                self.root.geometry(geometry)
                self.root.update()
                for widget in (*self.app.check_frame.winfo_children(), *self.app.split_frame.winfo_children()):
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertGreaterEqual(widget.winfo_rootx(), self.root.winfo_rootx())
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                         self.root.winfo_rootx() + self.root.winfo_width())
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                         self.root.winfo_rooty() + self.root.winfo_height())

    def test_submit_forwards_auto_split_and_each_thread_choice(self):
        # 隔离 GUI 参数构造；核心算法和 DownloadOptions 字段由核心测试验证。
        with patch('vk_video_download.DownloadOptions', side_effect=SimpleNamespace) as option_type:
            for auto_split in (True, False):
                for threads in (1, 2, 4, 8):
                    with self.subTest(auto_split=auto_split, threads=threads):
                        self.app.var_auto_split.set(auto_split)
                        self.app.fragments_var.set(str(threads))
                        self.app.entry_nm.insert('1.0', 'https://example.com/video')
                        self.app.get_directory_string()
                        option_type.assert_called_with(directory=self.app.download_dir, random_name=False,
                            limit_length=False, use_folder=False, proxy=None,
                            fragments=threads, auto_split=auto_split)
                        action, text, options = self.manager.calls[-1]
                        self.assertEqual(action, 'submit')
                        self.assertEqual(text.strip(), 'https://example.com/video')
                        self.assertEqual(options.auto_split, auto_split)
                        self.assertEqual(options.fragments, threads)
                        self.assertEqual(self.app.entry_nm.get('1.0', 'end').strip(), '')
                        self.assertTrue(self.app.tree.exists(self.manager.jobs[-1].id))

    def test_details_show_actual_layout_not_selected_settings(self):
        self.app.fragments_var.set('8')
        self.show_job(job('failed', part_count=7, active_workers=2, part_size=32 * 1024 * 1024,
                          error='测试错误'))
        _, y, _, height = self.app.tree.bbox('#1', 'name')
        self.assertTrue(self.app.tree.bind('<Double-1>'))
        with patch('vk_video_download.messagebox.showinfo') as info:
            self.app.show_job_details(SimpleNamespace(y=y + height // 2))
        info.assert_called_once()
        self.assertEqual(info.call_args.args[0], '任务详情')
        text = info.call_args.args[1]
        self.assertIn('实际分片数：7', text)
        self.assertIn('实际工作线程：2', text)
        self.assertIn('最大片大小：32.0 MB（33554432 字节）', text)
        self.assertIn('测试错误', text)
        self.assertNotIn('实际工作线程：8', text)
        self.assertIs(info.call_args.kwargs['parent'], self.root)

    def test_details_omit_each_zero_or_unknown_layout_field(self):
        self.show_job(job('downloading'))
        _, y, _, height = self.app.tree.bbox('#1', 'name')
        cases = (
            ({}, (False, False, False)),
            ({'part_count': 0, 'active_workers': 0, 'part_size': 0}, (False, False, False)),
            ({'part_count': 5, 'active_workers': 0, 'part_size': 0}, (True, False, False)),
            ({'part_count': 0, 'active_workers': 2, 'part_size': 0}, (False, True, False)),
            ({'part_count': 0, 'active_workers': 0, 'part_size': 1}, (False, False, True)),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.manager.jobs = [job('downloading', **fields)]
                with patch('vk_video_download.messagebox.showinfo') as info:
                    self.app.show_job_details(SimpleNamespace(y=y + height // 2))
                text = info.call_args.args[1]
                for label, present in zip(('实际分片数：', '实际工作线程：', '最大片大小：'), expected):
                    self.assertEqual(label in text, present)

    def test_failed_row_retry_button(self):
        self.show_job(job('failed'))
        self.assertEqual(self.app.tree.item('#1', 'values')[-1], '重试')
        self.click('action')
        self.assertEqual(self.manager.calls, [('retry', '#1', None)])

    def test_paused_and_running_actions(self):
        self.show_job(job('paused'))
        self.assertEqual(self.app.tree.item('#1', 'values')[-1], '继续')
        self.click('action')
        self.manager.jobs = [job('downloading', downloaded=64, total=128, speed=1024, eta=61)]
        self.manager.updates = self.manager.jobs[:]
        self.pump()
        self.assertEqual(self.app.tree.item('#1', 'values')[-1], '暂停')
        self.assertIn('50.0%', self.app.tree.item('#1', 'values')[4])
        self.assertIn('01:01', self.app.tree.item('#1', 'values')[3])
        self.click('action')
        self.assertEqual(self.manager.calls, [('resume', '#1'), ('pause', '#1')])

    def test_cancel_requires_explicit_hit_and_confirmation(self):
        self.show_job(job('paused'))
        with patch('vk_video_download.messagebox.askyesno', return_value=False) as confirm:
            self.click('status', fraction=0.2)
            confirm.assert_not_called()
            self.click('status', fraction=0.95)
            confirm.assert_called_once()
        self.assertEqual(self.manager.calls, [])

    def test_speed_hidden_when_not_downloading(self):
        self.assertEqual(speed_text(job('failed', speed=1024, eta=50)), '-')
        self.assertEqual(action_for_status('failed'), '重试')

    def test_auto_retry_status_has_no_manual_retry_action(self):
        self.show_job(job('retrying'))
        values = self.app.tree.item('#1', 'values')
        self.assertIn('自动重试中', values[4])
        self.assertEqual(values[-1], '')
        self.click('action')
        self.assertEqual(self.manager.calls, [])


if __name__ == '__main__':
    unittest.main()
