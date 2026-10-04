from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import re
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

import range_download
from download_core import DownloadManager, DownloadOptions
import time


CONTENT = bytes(range(256)) * 3


class RangeHandler(BaseHTTPRequestHandler):
    requests_seen = []
    fail_once = set()
    interrupt_once = set()
    def do_GET(self):
        value = self.headers.get('Range', '')
        self.requests_seen.append(value)
        if value in self.fail_once:
            self.fail_once.remove(value)
            self.send_error(503)
            return
        match = re.fullmatch(r'bytes=(\d+)-(\d+)', value)
        if not match:
            self.send_error(400)
            return
        start, end = map(int, match.groups())
        content = CONTENT[::-1] if 'changed' in self.path else CONTENT
        block = content[start:end + 1]
        self.send_response(206)
        self.send_header('Content-Range', f'bytes {start}-{end}/{len(content)}')
        self.send_header('Content-Length', str(len(block)))
        if 'signed' not in self.path:
            self.send_header('ETag', '"test"')
        self.end_headers()
        if value in self.interrupt_once:
            self.interrupt_once.remove(value)
            self.wfile.write(block[:len(block) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(block)
    def log_message(self, *args):
        pass


class RangeTests(unittest.TestCase):
    def test_default_chunk_size_is_64_mib(self):
        self.assertEqual(range_download.CHUNK_SIZE, 64 * 1024 * 1024)

    def setUp(self):
        RangeHandler.requests_seen = []
        RangeHandler.fail_once = set()
        RangeHandler.interrupt_once = set()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), RangeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = f'http://127.0.0.1:{self.server.server_port}/video.mp4'

    def test_64_unit_chunks_merge_and_resume(self):
        source = range_download.probe(self.url, proxy='')
        self.assertEqual(source.size, len(CONTENT))
        destination = Path(self.temp.name) / 'video.mp4'
        pause = threading.Event()
        with self.assertRaises(range_download.TransferStopped):
            range_download.download(source, destination, proxy='', pause=pause, chunk_size=64,
                max_workers=4, on_progress=lambda done, total: pause.set() if done >= 64 else None)
        self.assertFalse(destination.exists())
        self.assertTrue(Path(str(destination) + '.vkparts').exists())
        pause.clear()
        range_download.download(source, destination, proxy='', pause=pause, chunk_size=64, max_workers=4)
        self.assertEqual(destination.read_bytes(), CONTENT)
        self.assertFalse(Path(str(destination) + '.vkparts').exists())
        self.assertGreaterEqual(len(RangeHandler.requests_seen), 13)  # probe + 12 chunks

    def test_manager_selects_range_backend_for_large_http_media(self):
        url = self.url
        destination = Path(self.temp.name) / 'result.mp4'
        class FakeExtractor:
            def __init__(self, options):
                self.options = options
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def extract_info(self, input_url, download=False):
                return {'url': url, 'protocol': 'http', 'title': 'test', 'ext': 'mp4',
                        'http_headers': {}}
            def prepare_filename(self, info):
                return str(destination)
            def process_info(self, info):
                raise AssertionError('大文件应使用 Range 后端')
        with patch.object(range_download, 'CHUNK_SIZE', 64):
            manager = DownloadManager(max_workers=1, ydl_factory=FakeExtractor)
            self.addCleanup(manager.shutdown)
            job = manager.submit(url, DownloadOptions(self.temp.name, proxy='', fragments=4))[0]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = next(item.status for item in manager.snapshot() if item.id == job.id)
                if state in ('completed', 'failed'):
                    break
                time.sleep(0.01)
            self.assertEqual(state, 'completed')
            self.assertEqual(destination.read_bytes(), CONTENT)

    def test_existing_partial_chunk_resumes_at_byte_offset(self):
        source = range_download.probe(self.url, proxy='')
        destination = Path(self.temp.name) / 'continued.mp4'
        parts = Path(str(destination) + '.vkparts')
        parts.mkdir()
        (parts / 'manifest.json').write_text(json.dumps({'size': source.size, 'etag': source.etag,
            'url_hash': None}), encoding='utf-8')
        (parts / '000000.part').write_bytes(CONTENT[:10])
        range_download.download(source, destination, proxy='', chunk_size=64, max_workers=4)
        self.assertEqual(destination.read_bytes(), CONTENT)
        self.assertIn('bytes=10-63', RangeHandler.requests_seen)

    def test_signed_url_rotation_validates_existing_bytes(self):
        old = self.url + '?signed=old'
        new = self.url + '?signed=new'
        source = range_download.probe(new, proxy='')
        destination = Path(self.temp.name) / 'rotated.mp4'
        parts = Path(str(destination) + '.vkparts')
        parts.mkdir()
        (parts / 'manifest.json').write_text(json.dumps({'size': source.size, 'etag': None,
            'url_hash': hashlib.sha256(old.encode()).hexdigest()}), encoding='utf-8')
        (parts / '000000.part').write_bytes(CONTENT[:20])
        range_download.download(source, destination, proxy='', chunk_size=64, max_workers=4)
        self.assertEqual(destination.read_bytes(), CONTENT)

    def test_signed_url_rotation_rejects_changed_media(self):
        old = self.url + '?signed=old'
        changed = self.url + '?signed=changed'
        source = range_download.probe(changed, proxy='')
        destination = Path(self.temp.name) / 'changed.mp4'
        parts = Path(str(destination) + '.vkparts')
        parts.mkdir()
        (parts / 'manifest.json').write_text(json.dumps({'size': source.size, 'etag': None,
            'url_hash': hashlib.sha256(old.encode()).hexdigest()}), encoding='utf-8')
        (parts / '000000.part').write_bytes(CONTENT[:20])
        with self.assertRaisesRegex(ValueError, '远程文件已变化'):
            range_download.download(source, destination, proxy='', chunk_size=64, max_workers=4)
        self.assertFalse(destination.exists())
        self.assertEqual((parts / '000000.part').read_bytes(), CONTENT[:20])

    def test_probe_retries_transient_server_failure(self):
        RangeHandler.fail_once.add('bytes=0-0')
        with patch('time.sleep', return_value=None):
            source = range_download.probe(self.url, proxy='')
        self.assertEqual(source.size, len(CONTENT))
        self.assertEqual(RangeHandler.requests_seen.count('bytes=0-0'), 2)

    def test_probe_timeout_is_reported_instead_of_falling_back(self):
        session = MagicMock()
        session.__enter__.return_value.get.side_effect = requests.ReadTimeout('timed out')
        with patch.object(range_download, '_session', return_value=session), patch('time.sleep', return_value=None):
            with self.assertRaises(requests.ReadTimeout):
                range_download.probe(self.url, proxy='')
        self.assertEqual(session.__enter__.return_value.get.call_count, 3)

    def test_chunk_retries_503_without_losing_progress(self):
        source = range_download.probe(self.url, proxy='')
        RangeHandler.fail_once.add('bytes=0-63')
        destination = Path(self.temp.name) / 'retry.mp4'
        with patch('time.sleep', return_value=None):
            range_download.download(source, destination, proxy='', chunk_size=64, max_workers=1)
        self.assertEqual(destination.read_bytes(), CONTENT)
        self.assertEqual(RangeHandler.requests_seen.count('bytes=0-63'), 2)

    def test_chunk_retries_interrupted_stream_from_saved_offset(self):
        source = range_download.probe(self.url, proxy='')
        RangeHandler.interrupt_once.add('bytes=0-63')
        destination = Path(self.temp.name) / 'interrupted.mp4'
        with patch('time.sleep', return_value=None):
            range_download.download(source, destination, proxy='', chunk_size=64, max_workers=1)
        self.assertEqual(destination.read_bytes(), CONTENT)
        self.assertIn('bytes=32-63', RangeHandler.requests_seen)


if __name__ == '__main__':
    unittest.main()
