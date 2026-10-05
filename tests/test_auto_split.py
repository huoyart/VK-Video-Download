from http.server import ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import tempfile
import threading
import time
import unittest

from download_core import DownloadManager, DownloadOptions
import range_download
from test_range_download import CONTENT, RangeHandler


MIB = 1024 * 1024


class AutoSplitPlanTests(unittest.TestCase):
    def test_count_tracks_size_and_never_underuses_threads(self):
        for size, workers, count in ((30 * MIB, 8, 8), (64 * MIB, 4, 4),
                                     (128 * MIB, 8, 8), (1024 * MIB, 4, 16),
                                     (17 * 1024 * MIB // 10, 8, 28)):
            with self.subTest(size=size, workers=workers):
                plan = range_download.plan_chunks(size, workers)
                self.assertEqual(plan.count, count)
                self.assertGreaterEqual(plan.count, workers)
                self.assertLessEqual(plan.max_part_size, 64 * MIB)

    def test_balanced_ranges_cover_every_byte_once(self):
        plan = range_download.plan_chunks(101, 8, chunk_size=64)
        sizes = []
        cursor = 0
        for index in range(plan.count):
            first, last = plan.bounds(index)
            self.assertEqual(first, cursor)
            self.assertGreaterEqual(last, first)
            sizes.append(last - first + 1)
            cursor = last + 1
        self.assertEqual(cursor, 101)
        self.assertEqual(plan.count, 8)
        self.assertLessEqual(max(sizes) - min(sizes), 1)

    def test_tiny_file_limits_effective_threads_to_nonempty_parts(self):
        plan = range_download.plan_chunks(3, 8)
        self.assertEqual(plan.count, 3)
        self.assertEqual([plan.bounds(i) for i in range(3)], [(0, 0), (1, 1), (2, 2)])

    def test_fixed_mode_preserves_64_unit_boundaries(self):
        plan = range_download.plan_chunks(101, 8, chunk_size=64, auto_split=False)
        self.assertEqual(plan.count, 2)
        self.assertEqual([plan.bounds(i) for i in range(2)], [(0, 63), (64, 100)])

    def test_invalid_config_is_rejected(self):
        for args in ((0, 4, 64), (10, 0, 64), (10, 4, 0), (10, 2.5, 64)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                range_download.plan_chunks(*args)


class AutoSplitHTTPTests(unittest.TestCase):
    def setUp(self):
        RangeHandler.requests_seen = []
        RangeHandler.fail_once = set()
        RangeHandler.interrupt_once = set()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), RangeHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = f'http://127.0.0.1:{self.server.server_port}/video.mp4'
        self.destination = Path(self.temp.name) / 'video.mp4'

    def test_small_media_gets_one_part_per_thread_and_merges(self):
        source = range_download.probe(self.url, proxy='')
        observed = []
        range_download.download(source, self.destination, proxy='', max_workers=8,
                                on_plan=lambda plan, workers: observed.append((plan.count, workers)))
        self.assertEqual(observed, [(8, 8)])
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        for index in range(8):
            self.assertIn(f'bytes={index * 96}-{(index + 1) * 96 - 1}', RangeHandler.requests_seen)

    def test_uneven_ranges_merge_without_gaps(self):
        source = range_download.probe(self.url, proxy='')
        plan = range_download.plan_chunks(source.size, 4, chunk_size=70)
        range_download.download(source, self.destination, proxy='', max_workers=4, chunk_size=70)
        self.assertEqual(plan.count, 11)
        for index in range(plan.count):
            first, last = plan.bounds(index)
            self.assertIn(f'bytes={first}-{last}', RangeHandler.requests_seen)
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_saved_layout_survives_pause_and_thread_reduction(self):
        source = range_download.probe(self.url, proxy='')
        pause = threading.Event()
        with self.assertRaises(range_download.TransferStopped):
            range_download.download(source, self.destination, proxy='', max_workers=8, pause=pause,
                                    on_plan=lambda plan, workers: pause.set())
        parts = Path(str(self.destination) + '.vkparts')
        manifest = json.loads((parts / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['layout'], {'mode': 'balanced', 'count': 8})
        (parts / '000000.part').write_bytes(CONTENT[:10])
        pause.clear()
        observed = []
        range_download.download(source, self.destination, proxy='', max_workers=2, pause=pause,
                                auto_split=False,
                                on_plan=lambda plan, workers: observed.append((plan.count, workers)))
        self.assertEqual(observed, [(8, 2)])
        self.assertIn('bytes=10-95', RangeHandler.requests_seen)
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_signed_url_rotation_uses_saved_balanced_ranges(self):
        old_url = self.url + '?signed=old'
        source = range_download.probe(self.url + '?signed=new', proxy='')
        parts = Path(str(self.destination) + '.vkparts')
        parts.mkdir()
        manifest = {'size': source.size, 'etag': None,
                    'url_hash': hashlib.sha256(old_url.encode()).hexdigest(),
                    'layout': {'mode': 'balanced', 'count': 8}}
        (parts / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (parts / '000001.part').write_bytes(CONTENT[96:106])
        range_download.download(source, self.destination, proxy='', max_workers=2)
        self.assertIn('bytes=96-105', RangeHandler.requests_seen)
        self.assertIn('bytes=106-191', RangeHandler.requests_seen)
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_legacy_manifest_keeps_fixed_layout(self):
        source = range_download.probe(self.url, proxy='')
        parts = Path(str(self.destination) + '.vkparts')
        parts.mkdir()
        (parts / 'manifest.json').write_text(json.dumps({'size': source.size,
            'etag': source.etag, 'url_hash': None}), encoding='utf-8')
        (parts / '000000.part').write_bytes(CONTENT[:10])
        observed = []
        range_download.download(source, self.destination, proxy='', max_workers=8,
                                on_plan=lambda plan, workers: observed.append((plan.count, workers)))
        self.assertEqual(observed, [(1, 1)])
        self.assertIn('bytes=10-767', RangeHandler.requests_seen)
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_corrupt_saved_layout_leaves_partial_data_untouched(self):
        source = range_download.probe(self.url, proxy='')
        parts = Path(str(self.destination) + '.vkparts')
        parts.mkdir()
        (parts / 'manifest.json').write_text(json.dumps({'size': source.size,
            'etag': source.etag, 'url_hash': None, 'layout': {'mode': 'balanced', 'count': 0}}), encoding='utf-8')
        (parts / '000000.part').write_bytes(CONTENT[:10])
        with self.assertRaisesRegex(ValueError, '布局'):
            range_download.download(source, self.destination, proxy='', max_workers=8)
        self.assertEqual((parts / '000000.part').read_bytes(), CONTENT[:10])
        self.assertFalse(self.destination.exists())

    def test_manager_small_media_uses_auto_range_but_fixed_mode_falls_back(self):
        destination, url = self.destination, self.url
        class Extractor:
            fallback_calls = 0
            def __init__(self, options): pass
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def extract_info(self, *args, **kwargs):
                return {'url': url, 'protocol': 'http', 'title': 'local', 'ext': 'mp4'}
            def prepare_filename(self, info): return str(destination)
            def process_info(self, info):
                type(self).fallback_calls += 1
                destination.write_bytes(CONTENT)
        manager = DownloadManager(max_workers=1, ydl_factory=Extractor)
        self.addCleanup(manager.shutdown)
        for auto in (True, False):
            job = manager.submit(self.url, DownloadOptions(self.temp.name, proxy='', fragments=8, auto_split=auto))[0]
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                snapshot = next(item for item in manager.snapshot() if item.id == job.id)
                if snapshot.status == 'completed':
                    break
                time.sleep(0.01)
            self.assertEqual(snapshot.status, 'completed')
            self.assertEqual((snapshot.part_count, snapshot.active_workers), (8, 8) if auto else (0, 0))
        self.assertEqual(Extractor.fallback_calls, 1)


if __name__ == '__main__':
    unittest.main()
