"""固定工作线程和可合并的状态快照；后台代码不依赖 Tk。"""
from dataclasses import dataclass, field, replace
from pathlib import Path
from queue import Empty, Queue
import logging
import re
import threading
import time
import uuid

import yt_dlp

from download_utils import build_output_template, parse_video_urls, validate_proxy
import range_download

logger = logging.getLogger(__name__)
TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
_UNSET = object()


def describe_error(exc):
    """返回用户可读的原因及是否值得自动重试，不显示签名链接或代理凭据。"""
    message = str(exc).lower()
    if '远程文件已变化' in message or '已有分片缺少' in message:
        return '远程视频内容已变化，旧分片不能继续；请重新提交链接', False
    if 'range' in message or '分片' in message:
        return '服务器分片响应异常，请重试或降低并发数', True
    if re.search(r'\b(401|403)\b|forbidden|unauthorized', message):
        return '访问被拒绝；请检查视频权限、登录状态或代理', False
    if re.search(r'\b404\b|not found', message):
        return '视频链接或媒体文件已失效', False
    if re.search(r'\b(429|500|502|503|504)\b|timed? out|timeout|connection|reset|temporar', message):
        return '网络或站点暂时异常，可重试', True
    if 'ffmpeg' in message:
        return '音视频合并失败；请检查 FFmpeg', False
    return f'{type(exc).__name__}：请双击任务查看状态并尝试重试', False


class PauseRequested(Exception):
    """进度回调协作式暂停。"""


@dataclass(frozen=True)
class DownloadOptions:
    directory: str
    random_name: bool = False
    limit_length: bool = False
    use_folder: bool = False
    proxy: str | None = ''  # None=使用系统代理，''=明确直连
    fragments: int = 4
    auto_split: bool = True


@dataclass
class DownloadJob:
    id: str
    url: str
    options: DownloadOptions
    status: str = 'queued'
    title: str = ''
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: float | None = None
    error: str = ''
    output_template: str = ''
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    pause: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: threading.Event = field(default_factory=threading.Event, repr=False)
    last_progress: float = 0.0
    rate_at: float = 0.0
    rate_bytes: int = 0
    retryable: bool = False
    part_count: int = 0
    part_size: int = 0
    active_workers: int = 0


class DownloadManager:
    """只启动 max_workers 个线程；每个任务最多保留一条待刷新的快照。"""

    def __init__(self, max_workers=3, max_pending=1000, ydl_factory=None):
        if max_workers < 1 or max_pending < 1:
            raise ValueError('线程和队列上限必须大于零')
        self._factory = ydl_factory or yt_dlp.YoutubeDL
        self._lock = threading.RLock()
        self._queue = Queue(maxsize=max_pending)
        self._closing = threading.Event()
        self._jobs = {}
        self._updates = {}
        self._serial = 0
        self._threads = [threading.Thread(target=self._worker, name=f'vk-download-{i}', daemon=True)
                         for i in range(max_workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, text, options):
        urls = parse_video_urls(text)
        if options.fragments not in (1, 2, 4, 8):
            raise ValueError('分片并发数应为 1、2、4 或 8')
        if type(options.auto_split) is not bool:
            raise ValueError('自动分片选项应为布尔值')
        options = replace(options, proxy=validate_proxy(options.proxy) if options.proxy is not None else None)
        with self._lock:
            if self._closing.is_set():
                raise ValueError('下载器正在关闭')
            active_urls = {job.url for job in self._jobs.values() if job.status not in TERMINAL}
            urls = [url for url in urls if url not in active_urls]
            if len(urls) > self._queue.maxsize - self._queue.qsize():
                raise ValueError('等待队列已满，请减少链接数量或等待任务完成')
            jobs = []
            for url in urls:
                self._serial += 1
                job = DownloadJob(f'#{self._serial}', url, options)
                job.output_template = build_output_template(options.directory,
                    random_name=options.random_name, limit_length=options.limit_length,
                    use_folder=options.use_folder, token=uuid.uuid4().hex[:12])
                self._jobs[job.id] = job
                jobs.append(replace(job))
                self._publish(job)
                self._queue.put_nowait(job)
            return jobs

    def _publish(self, job):
        # 调用方持有 RLock，快照字段均为不可变值（Event 仅用于控制）。
        self._updates[job.id] = replace(job)

    def drain_updates(self):
        with self._lock:
            updates = list(self._updates.values())
            self._updates.clear()
            return updates

    def snapshot(self):
        with self._lock:
            return [replace(job) for job in self._jobs.values()]

    def clear_finished(self):
        with self._lock:
            ids = [key for key, job in self._jobs.items() if job.finished.is_set()]
            for key in ids:
                del self._jobs[key]
                self._updates.pop(key, None)
            return ids

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in TERMINAL:
                return False
            job.stop.set()
            if job.status in ('queued', 'paused'):
                job.status = 'cancelled'
                job.finished.set()
            else:
                job.status = 'cancelling'
            self._publish(job)
            return True

    def pause(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in TERMINAL or job.status in ('processing', 'paused', 'pausing', 'cancelling'):
                return False
            job.pause.set()
            if job.status == 'queued':
                job.status = 'paused'
            else:
                job.status = 'pausing'
            self._publish(job)
            return True

    def resume(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != 'paused' or self._closing.is_set() or self._queue.full():
                return False
            job.pause.clear()
            job.status = 'queued'
            job.rate_at = 0.0
            job.speed = None
            job.eta = None
            self._publish(job)
            self._queue.put_nowait(job)
            return True

    def retry(self, job_id, *, proxy=_UNSET):
        """失败任务复用原输出模板/分片；可选切换代理后重试。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != 'failed' or self._closing.is_set() or self._queue.full():
                return False
            if proxy is not _UNSET:
                job.options = replace(job.options, proxy=validate_proxy(proxy) if proxy is not None else None)
            job.stop.clear()
            job.pause.clear()
            job.finished.clear()
            job.error = ''
            job.retryable = False
            job.speed = None
            job.eta = None
            job.rate_at = 0.0
            job.status = 'queued'
            self._publish(job)
            self._queue.put_nowait(job)
            return True

    def shutdown(self):
        with self._lock:
            self._closing.set()
            for job_id in list(self._jobs):
                self.cancel(job_id)

    def is_idle(self):
        with self._lock:
            return all(job.finished.is_set() or job.status == 'paused' for job in self._jobs.values())

    @staticmethod
    def _check_cancel(job):
        if job.stop.is_set():
            raise yt_dlp.utils.DownloadError('Cancelled by user')
        if job.pause.is_set():
            raise PauseRequested()

    def _progress(self, job, data):
        self._check_cancel(job)
        now = time.monotonic()
        with self._lock:
            if job.status in TERMINAL:
                return
            info = data.get('info_dict') or {}
            job.title = str(info.get('title') or job.title or job.url).replace('\n', ' ').replace('\r', ' ')
            job.downloaded = data.get('downloaded_bytes') or 0
            job.total = data.get('total_bytes') or data.get('total_bytes_estimate')
            reported_speed = data.get('speed')
            if reported_speed and reported_speed > 0:
                job.speed = float(reported_speed)
            elif job.rate_at == 0 or job.downloaded < job.rate_bytes:
                reset = job.downloaded < job.rate_bytes
                job.rate_at = now
                job.rate_bytes = job.downloaded
                if reset:
                    job.speed = None
            elif now - job.rate_at >= 0.25:
                delta = job.downloaded - job.rate_bytes
                if delta > 0:
                    measured = delta / (now - job.rate_at)
                    job.speed = measured if not job.speed else 0.5 * job.speed + 0.5 * measured
                job.rate_at = now
                job.rate_bytes = job.downloaded
            job.eta = data.get('eta')
            if job.eta is None and job.speed and job.total and job.total > job.downloaded:
                job.eta = (job.total - job.downloaded) / job.speed
            # finished 只代表一个媒体流下载结束，合并/后处理仍可能失败。
            job.status = 'processing' if data.get('status') == 'finished' else 'downloading'
            if job.status == 'processing':
                job.speed = None
                job.eta = None
            if job.status == 'processing' or now - job.last_progress >= 0.1:
                job.last_progress = now
                self._publish(job)

    def _postprocess(self, job, data):
        self._check_cancel(job)
        with self._lock:
            job.status = 'processing'
            self._publish(job)

    def _range_merge(self, job):
        with self._lock:
            job.status = 'processing'
            job.speed = None
            job.eta = None
            self._publish(job)

    def _range_plan(self, job, plan, workers):
        with self._lock:
            job.part_count = plan.count
            job.part_size = plan.max_part_size
            job.active_workers = workers
            self._publish(job)

    def _worker(self):
        while not self._closing.is_set():
            try:
                job = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                with self._lock:
                    if job.stop.is_set() or job.status != 'queued':
                        continue
                    job.status = 'extracting'
                    self._publish(job)
                self._download(job)
                with self._lock:
                    auto_retry = job.status == 'failed' and job.retryable and not self._closing.is_set()
                    if auto_retry:
                        job.finished.clear()
                        job.status = 'retrying'
                        job.error = '网络暂时异常，正在自动重试（1/1）'
                        self._publish(job)
                if auto_retry:
                    if job.stop.wait(0.5):
                        with self._lock:
                            job.status = 'cancelled'
                            job.finished.set()
                            self._publish(job)
                    else:
                        with self._lock:
                            # CDN 超时时降低并发，避免重试继续压满同一连接。
                            if job.options.fragments > 1:
                                job.options = replace(job.options, fragments=max(1, job.options.fragments // 2))
                            job.status = 'extracting'
                            job.error = ''
                            job.rate_at = 0.0
                            job.speed = None
                            job.eta = None
                            self._publish(job)
                        self._download(job)
            finally:
                self._queue.task_done()

    def _download(self, job):
        try:
            self._check_cancel(job)
            with self._lock:
                job.part_count = job.part_size = job.active_workers = 0
            Path(job.options.directory).mkdir(parents=True, exist_ok=True)
            options = {
                'outtmpl': job.output_template,
                # HLS/DASH 分片并发下载；分离音视频由 yt-dlp 调用 FFmpeg 合并。
                'concurrent_fragment_downloads': job.options.fragments,
                'format': 'bv*+ba/b',
                'merge_output_format': 'mp4/mkv',
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'windowsfilenames': True,
                'continuedl': True,
                'nopart': False,
                'socket_timeout': 40,
                'retries': 5,
                'fragment_retries': 5,
                'extractor_retries': 5,
                'overwrites': False,
                'progress_hooks': [lambda data: self._progress(job, data)],
                'postprocessor_hooks': [lambda data: self._postprocess(job, data)],
            }
            # None=系统代理：不传 proxy，由 yt-dlp/环境变量发现；''=明确直连。
            if job.options.proxy is not None:
                options['proxy'] = job.options.proxy
            # 普通 HTTP 支持自动/固定 Range 分片；HLS/DASH 保留站点原生分片。
            with self._factory(options) as downloader:
                if hasattr(downloader, 'extract_info') and hasattr(downloader, 'process_info'):
                    info = downloader.extract_info(job.url, download=False)
                    self._check_cancel(job)
                    if not info:
                        raise RuntimeError('未获取到视频信息')
                    if not isinstance(info, dict) or info.get('_type') in ('playlist', 'multi_video'):
                        code = downloader.download([job.url])
                        info = None
                    if info is None:
                        pass
                    else:
                        with self._lock:
                            job.title = str(info.get('title') or job.url)
                            self._publish(job)
                        media_url = info.get('url', '')
                        progressive = (not info.get('requested_formats') and not info.get('fragments')
                                       and not info.get('is_live') and info.get('protocol') in ('http', 'https')
                                       and media_url.startswith(('http://', 'https://')))
                        source = range_download.probe(media_url, info.get('http_headers'), job.options.proxy,
                            stop=job.stop, pause=job.pause) if progressive else None
                        if source and source.size > 0 and (job.options.auto_split or source.size > range_download.CHUNK_SIZE):
                            with self._lock:
                                job.total = source.size
                                self._publish(job)
                            range_download.download(source, downloader.prepare_filename(info),
                                headers=info.get('http_headers'), proxy=job.options.proxy,
                                stop=job.stop, pause=job.pause, max_workers=job.options.fragments,
                                auto_split=job.options.auto_split,
                                on_plan=lambda plan, workers: self._range_plan(job, plan, workers),
                                on_progress=lambda done, total: self._progress(job, {
                                    'status': 'downloading', 'downloaded_bytes': done,
                                    'total_bytes': total, 'info_dict': info}),
                                on_merge=lambda: self._range_merge(job))
                        else:
                            downloader.process_info(info)
                        code = 0
                else:
                    # 模拟下载器及旧版 yt-dlp 兼容路径。
                    code = downloader.download([job.url])
            self._check_cancel(job)
            if code:
                raise RuntimeError(f'下载器返回错误代码 {code}')
            with self._lock:
                job.status = 'cancelled' if job.stop.is_set() else ('paused' if job.pause.is_set() else 'completed')
        except Exception as exc:
            with self._lock:
                job.status = 'cancelled' if job.stop.is_set() else ('paused' if job.pause.is_set() else 'failed')
                # 异常原文可能含代理密码/签名URL；UI只显示异常类别。
                job.error, job.retryable = ('', False) if job.stop.is_set() or job.pause.is_set() else describe_error(exc)
                if job.status == 'failed':
                    logger.warning('任务 %s 失败 (%s)', job.id, type(exc).__name__)
        finally:
            with self._lock:
                job.speed = None
                job.eta = None
                if job.status != 'paused':
                    job.finished.set()
                self._publish(job)
