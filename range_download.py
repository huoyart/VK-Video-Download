"""普通 HTTP 视频的 64 MiB Range 并发下载及可续传合并。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re
import threading
import time

import requests

CHUNK_SIZE = 64 * 1024 * 1024
MAX_WORKERS = 8
REQUEST_TIMEOUT = (10, 40)
PROBE_ATTEMPTS = 3
PART_ATTEMPTS = 5


class TransferStopped(Exception):
    pass


@dataclass(frozen=True)
class RangeSource:
    url: str
    size: int
    etag: str | None


def _session(proxy):
    session = requests.Session()
    if proxy is None:
        return session
    if proxy == '':
        session.trust_env = False
    else:
        session.proxies.update({'http': proxy, 'https': proxy})
        session.trust_env = False
    return session


def _wait_retry(attempt, stop=None, pause=None):
    if (stop and stop.is_set()) or (pause and pause.is_set()):
        raise TransferStopped()
    time.sleep(min(0.5 * (2 ** (attempt - 1)), 4))
    if (stop and stop.is_set()) or (pause and pause.is_set()):
        raise TransferStopped()


def probe(url, headers=None, proxy=None, stop=None, pause=None):
    """206 且 Content-Range 总长可信时才启用 Range；否则交回 yt-dlp。"""
    request_headers = dict(headers or {})
    request_headers.update({'Range': 'bytes=0-0', 'Accept-Encoding': 'identity'})
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        if (stop and stop.is_set()) or (pause and pause.is_set()):
            raise TransferStopped()
        try:
            with _session(proxy) as session:
                with session.get(url, headers=request_headers, stream=True, timeout=REQUEST_TIMEOUT) as response:
                    if response.status_code in (429, 500, 502, 503, 504, 408):
                        response.raise_for_status()
                    if response.status_code in (401, 403, 404):
                        response.raise_for_status()
                    if response.status_code != 206:
                        return None
                    match = re.fullmatch(r'bytes 0-0/(\d+)', response.headers.get('Content-Range', ''))
                    if not match:
                        return None
                    etag = response.headers.get('ETag')
                    if etag and etag.startswith('W/'):
                        etag = None
                    return RangeSource(url, int(match.group(1)), etag)
        except requests.RequestException as exc:
            if attempt == PROBE_ATTEMPTS or '401' in str(exc) or '403' in str(exc) or '404' in str(exc):
                raise
            _wait_retry(attempt, stop, pause)


def _validate_rotated_url(parts_dir, source, chunk_size, headers, proxy):
    """签名 URL 更新时，用每个已有分片的头尾字节确认仍为同一资源。"""
    paths = sorted(parts_dir.glob('*.part'))
    if not paths:
        return
    with _session(proxy) as session:
        for path in paths:
            try:
                index = int(path.stem)
            except ValueError:
                raise ValueError('已有分片名称无效') from None
            start = index * chunk_size
            expected = min(chunk_size, source.size - start)
            length = path.stat().st_size
            if expected <= 0 or length > expected:
                raise ValueError('已有分片长度与远程文件不匹配')
            if not length:
                continue
            sample = min(16 * 1024, length)
            offsets = {0, length - sample}
            with path.open('rb') as file:
                for offset in offsets:
                    file.seek(offset)
                    local = file.read(sample)
                    first = start + offset
                    last = first + sample - 1
                    request_headers = dict(headers or {})
                    request_headers.update({'Range': f'bytes={first}-{last}', 'Accept-Encoding': 'identity'})
                    try:
                        with session.get(source.url, headers=request_headers, stream=True, timeout=REQUEST_TIMEOUT) as response:
                            if response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {first}-{last}/{source.size}':
                                raise ValueError('无法校验新链接与已有分片是否一致')
                            remote = response.content
                    except requests.RequestException as exc:
                        raise ValueError('校验已有分片时网络请求失败') from exc
                    if remote != local:
                        raise ValueError('远程文件已变化，已有分片不可继续')


def download(source, destination, headers=None, proxy=None, stop=None, pause=None,
             on_progress=None, on_merge=None, chunk_size=CHUNK_SIZE, max_workers=MAX_WORKERS):
    """每片以独立 part 文件保存；重试可续写，全部成功后顺序合并并原子替换。"""
    if chunk_size < 1 or max_workers < 1 or source.size < 1:
        raise ValueError('无效的分片配置')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = Path(str(destination) + '.vkparts')
    parts_dir.mkdir(parents=True, exist_ok=True)
    count = (source.size + chunk_size - 1) // chunk_size
    manifest = parts_dir / 'manifest.json'
    identity = {'size': source.size, 'etag': source.etag,
                'url_hash': None if source.etag else hashlib.sha256(source.url.encode('utf-8')).hexdigest()}
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding='utf-8'))
        if previous != identity:
            if previous.get('size') != source.size or (previous.get('etag') and previous['etag'] != source.etag):
                raise ValueError('远程文件已变化，已有分片需另行处理')
            _validate_rotated_url(parts_dir, source, chunk_size, headers, proxy)
            marker = parts_dir / 'manifest.new'
            marker.write_text(json.dumps(identity), encoding='utf-8')
            os.replace(marker, manifest)
    elif any(parts_dir.glob('*.part')):
        raise ValueError('已有分片缺少来源校验信息')
    else:
        marker = parts_dir / 'manifest.new'
        marker.write_text(json.dumps(identity), encoding='utf-8')
        os.replace(marker, manifest)
    stop = stop or threading.Event()
    pause = pause or threading.Event()
    lock = threading.Lock()
    transferred = 0

    def check_stop():
        if stop.is_set() or pause.is_set():
            raise TransferStopped()

    def get_part(index):
        nonlocal transferred
        check_stop()
        start = index * chunk_size
        end = min(source.size, start + chunk_size) - 1
        expected = end - start + 1
        path = parts_dir / f'{index:06d}.part'
        have = path.stat().st_size if path.exists() else 0
        if have > expected:
            raise ValueError('已有分片长度与远程文件不匹配')
        if have == expected:
            return path
        for attempt in range(1, PART_ATTEMPTS + 1):
            check_stop()
            have = path.stat().st_size if path.exists() else 0
            if have == expected:
                return path
            if have > expected:
                raise ValueError('已有分片长度与远程文件不匹配')
            request_headers = dict(headers or {})
            request_headers.update({'Range': f'bytes={start + have}-{end}', 'Accept-Encoding': 'identity'})
            if source.etag:
                request_headers['If-Range'] = source.etag
            try:
                with _session(proxy) as session:
                    with session.get(source.url, headers=request_headers, stream=True, timeout=REQUEST_TIMEOUT) as response:
                        if response.status_code in (408, 429, 500, 502, 503, 504):
                            response.raise_for_status()
                        wanted = f'bytes {start + have}-{end}/{source.size}'
                        if response.status_code != 206 or response.headers.get('Content-Range') != wanted:
                            raise ValueError('服务器未返回预期的 Range 分片')
                        with path.open('ab') as file:
                            for block in response.iter_content(chunk_size=min(128 * 1024, max(1, expected // 4))):
                                check_stop()
                                if block:
                                    file.write(block)
                                    with lock:
                                        transferred += len(block)
                                        if on_progress:
                                            on_progress(transferred, source.size)
                if path.stat().st_size != expected:
                    raise requests.ConnectionError('分片传输提前结束')
                return path
            except requests.RequestException:
                if attempt == PART_ATTEMPTS:
                    raise
                _wait_retry(attempt, stop, pause)
        raise RuntimeError('分片重试次数耗尽')

    # 已有分片只计入一次，真实网络数据单独累积。
    existing = 0
    for index in range(count):
        path = parts_dir / f'{index:06d}.part'
        if path.exists():
            existing += path.stat().st_size
    transferred = existing
    if on_progress:
        on_progress(transferred, source.size)
    with ThreadPoolExecutor(max_workers=min(max_workers, count)) as pool:
        futures = [pool.submit(get_part, index) for index in range(count)]
        for future in as_completed(futures):
            future.result()
    check_stop()
    if on_merge:
        on_merge()
    temp = Path(str(destination) + '.assembling')
    try:
        with temp.open('wb') as output:
            for index in range(count):
                part = parts_dir / f'{index:06d}.part'
                with part.open('rb') as input_file:
                    while block := input_file.read(1024 * 1024):
                        if stop.is_set():
                            raise TransferStopped()
                        output.write(block)
        if temp.stat().st_size != source.size:
            raise ValueError('合并后的文件长度校验失败')
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    for index in range(count):
        (parts_dir / f'{index:06d}.part').unlink()
    manifest.unlink()
    parts_dir.rmdir()
    return destination
