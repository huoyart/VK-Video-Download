import tempfile
import time
import unittest
from unittest.mock import patch

import yt_dlp
from download_core import DownloadJob, DownloadManager, DownloadOptions


URL = 'https://example.com/video'


def wait_status(manager, job_id, wanted, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = next(item.status for item in manager.snapshot() if item.id == job_id)
        if state == wanted:
            return
        time.sleep(0.01)
    raise AssertionError(f'期望 {wanted}，实际 {state}')


class RetrySpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_range_progress_calculates_speed_and_eta(self):
        manager = DownloadManager(max_workers=1)
        self.addCleanup(manager.shutdown)
        job = DownloadJob('#speed', URL, DownloadOptions(self.temp.name))
        with patch('download_core.time.monotonic', side_effect=[10.0, 10.5]):
            manager._progress(job, {'status': 'downloading', 'downloaded_bytes': 0, 'total_bytes': 2 * 1024 * 1024})
            manager._progress(job, {'status': 'downloading', 'downloaded_bytes': 1024 * 1024, 'total_bytes': 2 * 1024 * 1024})
        self.assertGreater(job.speed, 0)
        self.assertAlmostEqual(job.eta, 0.5)

    def test_failed_task_can_retry_with_same_output_template(self):
        class FailOnce:
            calls = []
            def __init__(self, options):
                self.options = options
                self.calls.append(options['outtmpl'])
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def download(self, urls):
                if len(self.calls) == 1:
                    raise ValueError('非临时错误')
                return 0
        manager = DownloadManager(max_workers=1, ydl_factory=FailOnce)
        self.addCleanup(manager.shutdown)
        job = manager.submit(URL, DownloadOptions(self.temp.name))[0]
        wait_status(manager, job.id, 'failed')
        self.assertTrue(manager.retry(job.id))
        wait_status(manager, job.id, 'completed')
        self.assertEqual(FailOnce.calls[0], FailOnce.calls[1])

    def test_transient_extract_error_retries_once(self):
        class TransientExtractor:
            count = 0
            options_seen = []
            def __init__(self, options): self.options_seen.append(options)
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def download(self, urls):
                type(self).count += 1
                if self.count == 1:
                    raise yt_dlp.utils.DownloadError('timed out')
                return 0
        manager = DownloadManager(max_workers=1, ydl_factory=TransientExtractor)
        self.addCleanup(manager.shutdown)
        job = manager.submit(URL, DownloadOptions(self.temp.name))[0]
        wait_status(manager, job.id, 'completed')
        self.assertEqual(TransientExtractor.count, 2)
        self.assertEqual([item['concurrent_fragment_downloads'] for item in TransientExtractor.options_seen], [4, 2])
        self.assertTrue(all(item['socket_timeout'] >= 40 and item['retries'] >= 5
                            for item in TransientExtractor.options_seen))

    def test_playlist_info_uses_standard_download_path(self):
        class PlaylistExtractor:
            calls = 0
            def __init__(self, options): pass
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def extract_info(self, url, download=False):
                return {'_type': 'playlist', 'entries': [{'url': URL}]}
            def process_info(self, info):
                raise AssertionError('不能将 playlist 传给 process_info')
            def download(self, urls):
                type(self).calls += 1
                return 0
        manager = DownloadManager(max_workers=1, ydl_factory=PlaylistExtractor)
        self.addCleanup(manager.shutdown)
        job = manager.submit(URL, DownloadOptions(self.temp.name))[0]
        wait_status(manager, job.id, 'completed')
        self.assertEqual(PlaylistExtractor.calls, 1)


if __name__ == '__main__':
    unittest.main()
