"""GUI 回归测试：用内存任务代替真实网络下载。"""
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from vk_video_download import App, action_for_status, speed_text


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
        if hasattr(self, 'root'):
            if hasattr(self, 'app') and self.app._poll_id is not None:
                self.root.after_cancel(self.app._poll_id)
            self.root.destroy()

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
