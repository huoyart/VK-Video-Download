"""普通 HTTP 视频的自动 Range 分片、并发下载及可续传合并。"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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


@dataclass(frozen=True)
class PartPlan:
    size: int
    count: int
    chunk_size: int | None = None  # None=均匀分片；非空=旧固定片长布局

    def bounds(self, index):
        if not 0 <= index < self.count:
            raise ValueError('已有分片编号超出布局范围')
        if self.chunk_size is not None:
            start = index * self.chunk_size
            return start, min(self.size, start + self.chunk_size) - 1
        quotient, remainder = divmod(self.size, self.count)
        start = index * quotient + min(index, remainder)
        return start, start + quotient + (index < remainder) - 1

    @property
    def max_part_size(self):
        return min(self.chunk_size, self.size) if self.chunk_size is not None else (self.size + self.count - 1) // self.count

    def to_dict(self):
        layout = {'mode': 'fixed' if self.chunk_size is not None else 'balanced', 'count': self.count}
        if self.chunk_size is not None:
            layout['chunk_size'] = self.chunk_size
        return layout


def plan_chunks(size, max_workers=MAX_WORKERS, chunk_size=None, auto_split=True):
    """新任务至少一片/有效线程，目标片长不超过 64 MiB，均匀覆盖所有字节。"""
    chunk_size = CHUNK_SIZE if chunk_size is None else chunk_size
    if any(type(value) is not int or value < 1 for value in (size, max_workers, chunk_size)):
        raise ValueError('无效的分片配置')
    count = (size + chunk_size - 1) // chunk_size
    if auto_split:
        count = min(size, max(max_workers, count))
        return PartPlan(size, count)
    return PartPlan(size, count, chunk_size)


def _saved_plan(size, layout):
    if not isinstance(layout, dict) or type(layout.get('count')) is not int or not 1 <= layout['count'] <= size:
        raise ValueError('已有分片布局无效')
    if layout.get('mode') == 'balanced':
        return PartPlan(size, layout['count'])
    if layout.get('mode') == 'fixed':
        chunk_size = layout.get('chunk_size')
        if type(chunk_size) is int and chunk_size > 0 and (size + chunk_size - 1) // chunk_size == layout['count']:
            return PartPlan(size, layout['count'], chunk_size)
    raise ValueError('已有分片布局无效')


def _validate_parts(parts_dir, plan):
    for path in parts_dir.glob('*.part'):
        if not re.fullmatch(r'\d{6,}', path.stem):
            raise ValueError('已有分片名称无效')
        first, last = plan.bounds(int(path.stem))
        if path.stat().st_size > last - first + 1:
            raise ValueError('已有分片长度与远程文件不匹配')


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


def _validate_rotated_url(parts_dir, source, plan, headers, proxy):
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
            start, end = plan.bounds(index)
            expected = end - start + 1
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
             on_progress=None, on_merge=None, chunk_size=None, max_workers=MAX_WORKERS,
             auto_split=True, on_plan=None):
    """每片以独立 part 文件保存；重试可续写，全部成功后顺序合并并原子替换。"""
    plan = plan_chunks(source.size, max_workers, chunk_size, auto_split)
    chunk_size = CHUNK_SIZE if chunk_size is None else chunk_size
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = Path(str(destination) + '.vkparts')
    parts_dir.mkdir(parents=True, exist_ok=True)
    manifest = parts_dir / 'manifest.json'
    identity = {'size': source.size, 'etag': source.etag,
                'url_hash': None if source.etag else hashlib.sha256(source.url.encode('utf-8')).hexdigest()}
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(previous, dict):
            raise ValueError('已有分片来源校验信息无效')
        changed_source = {key: previous.get(key) for key in identity} != identity
        if changed_source and (previous.get('size') != source.size or
                               (previous.get('etag') and previous['etag'] != source.etag)):
            raise ValueError('远程文件已变化，已有分片需另行处理')
        # 降并发、改变自动选项、签名 URL 轮换时均沿用原边界；旧版清单按固定片长升级。
        plan = (_saved_plan(source.size, previous['layout']) if 'layout' in previous
                else plan_chunks(source.size, max_workers, chunk_size, auto_split=False))
        _validate_parts(parts_dir, plan)
        if changed_source:
            _validate_rotated_url(parts_dir, source, plan, headers, proxy)
    elif any(parts_dir.glob('*.part')):
        raise ValueError('已有分片缺少来源校验信息')
    identity['layout'] = plan.to_dict()
    marker = parts_dir / 'manifest.new'
    marker.write_text(json.dumps(identity), encoding='utf-8')
    os.replace(marker, manifest)
    count = plan.count
    workers = min(max_workers, count)
    if on_plan:
        on_plan(plan, workers)
    stop = stop or threading.Event()
    pause = pause or threading.Event()
    lock = threading.Lock()
    abort = threading.Event()
    transferred = 0

    def check_stop():
        if stop.is_set() or pause.is_set() or abort.is_set():
            raise TransferStopped()

    def get_part(index):
        nonlocal transferred
        check_stop()
        start, end = plan.bounds(index)
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
    with ThreadPoolExecutor(max_workers=workers) as pool:
        indices = iter(range(count))
        pending = {pool.submit(get_part, next(indices)) for _ in range(workers)}
        try:
            while pending:
                completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
                    index = next(indices, None)
                    if index is not None:
                        pending.add(pool.submit(get_part, index))
        except BaseException:
            abort.set()
            for future in pending:
                future.cancel()
            raise
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
