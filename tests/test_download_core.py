import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from download_core import DownloadManager, DownloadOptions


URL = 'https://example.com/video'


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('等待状态超时')


class FakeDL:
    calls = []
    lock = threading.Lock()
    started = threading.Event()
    allow_finish = threading.Event()

    def __init__(self, options):
        self.options = options
        with self.lock:
            self.calls.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        assert urls == [URL]
        partial = Path(self.options['outtmpl'].replace('%(ext)s', 'mp4') + '.part')
        partial.parent.mkdir(parents=True, exist_ok=True)
        before = partial.stat().st_size if partial.exists() else 0
        self.started.set()
        partial.write_bytes(b'abcd' if before == 0 else b'abcdefgh')
        self.options['progress_hooks'][0]({'status': 'downloading', 'downloaded_bytes': partial.stat().st_size,
                                            'total_bytes': 8, 'info_dict': {'title': '测试视频'}})
        if before == 0 and not self.allow_finish.wait(2):
            self.options['progress_hooks'][0]({'status': 'downloading', 'downloaded_bytes': 4, 'total_bytes': 8})
        self.options['progress_hooks'][0]({'status': 'finished', 'downloaded_bytes': 8, 'total_bytes': 8})
        self.options['postprocessor_hooks'][0]({'status': 'started'})
        partial.rename(partial.with_suffix(''))
        return 0


class DownloadCoreTests(unittest.TestCase):
    def setUp(self):
        FakeDL.calls = []
        FakeDL.started.clear()
        FakeDL.allow_finish.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = DownloadManager(max_workers=1, ydl_factory=FakeDL)
        self.addCleanup(self.manager.shutdown)

    def status(self, job_id):
        return next(job.status for job in self.manager.snapshot() if job.id == job_id)

    def test_pause_resume_uses_same_partial_file(self):
        job = self.manager.submit(URL, DownloadOptions(self.temp.name))[0]
        self.assertTrue(FakeDL.started.wait(2))
        self.assertTrue(self.manager.pause(job.id))
        wait_until(lambda: self.status(job.id) == 'paused')
        self.assertFalse(self.manager.snapshot()[0].finished.is_set())
        self.assertTrue(self.manager.resume(job.id))
        FakeDL.allow_finish.set()
        wait_until(lambda: self.status(job.id) == 'completed')
        self.assertEqual(FakeDL.calls[0]['outtmpl'], FakeDL.calls[1]['outtmpl'])
        self.assertEqual(FakeDL.calls[0]['concurrent_fragment_downloads'], 4)
        self.assertTrue(FakeDL.calls[1]['continuedl'])
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.mp4'))), 1)

    def test_proxy_modes_and_fragment_merge_options(self):
        job = self.manager.submit(URL, DownloadOptions(self.temp.name, proxy=None, fragments=8))[0]
        self.assertTrue(FakeDL.started.wait(2))
        self.assertNotIn('proxy', FakeDL.calls[0])
        self.assertEqual(FakeDL.calls[0]['concurrent_fragment_downloads'], 8)
        self.assertEqual(FakeDL.calls[0]['format'], 'bv*+ba/b')
        self.assertEqual(FakeDL.calls[0]['merge_output_format'], 'mp4/mkv')
        FakeDL.allow_finish.set()
        wait_until(lambda: self.status(job.id) == 'completed')

    def test_queued_pause_and_cancel(self):
        first = self.manager.submit(URL, DownloadOptions(self.temp.name))[0]
        self.assertTrue(FakeDL.started.wait(2))
        second = self.manager.submit('https://example.com/next', DownloadOptions(self.temp.name))[0]
        self.assertTrue(self.manager.pause(second.id))
        self.assertEqual(self.status(second.id), 'paused')
        self.assertTrue(self.manager.cancel(second.id))
        self.assertEqual(self.status(second.id), 'cancelled')
        self.assertFalse(self.manager.resume(second.id))
        FakeDL.allow_finish.set()
        wait_until(lambda: self.status(first.id) == 'completed')

    def test_finished_hook_is_not_completion(self):
        from download_core import DownloadJob
        job = DownloadJob('#test', URL, DownloadOptions(self.temp.name))
        self.manager._progress(job, {'status': 'finished', 'downloaded_bytes': 8, 'total_bytes': 8})
        self.assertEqual(job.status, 'processing')
        self.assertFalse(job.finished.is_set())


if __name__ == '__main__':
    unittest.main()
