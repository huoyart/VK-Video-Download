"""不依赖 Tk 的链接、代理、文件名和输出模板工具。"""
import math
import os
import re
import uuid
from urllib.parse import urlsplit


def _valid_host_url(value, schemes, label):
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if parts.scheme.lower() not in schemes or not host or any(ord(char) < 33 for char in value):
            raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        host.encode('idna')
    except (ValueError, UnicodeError):
        raise ValueError(f'{label}格式无效') from None
    return parts


def parse_video_urls(text):
    """常用分隔符拆分链接，保留原顺序并去重。"""
    urls = list(dict.fromkeys(item for item in re.split(r'[,，\s]+', text.strip()) if item))
    if not urls:
        raise ValueError('请输入视频链接')
    for url in urls:
        _valid_host_url(url, {'http', 'https'}, '视频链接')
    return urls


def validate_proxy(value):
    """空值明确直连；系统代理由调用方以 None 表示。"""
    value = value.strip()
    if not value:
        return ''
    parts = _valid_host_url(value, {'http', 'https', 'socks5', 'socks5h'}, '代理地址')
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise ValueError('代理地址格式无效')
    return value


def sanitize_filename(name, max_len=120):
    """Windows 保留名、尾部句点与非法字符处理。"""
    if max_len < 1:
        raise ValueError('文件名长度必须大于零')
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', (name or '').strip())
    value = re.sub(r'\s+', ' ', value).strip(' .')[:max_len].rstrip(' .')
    if not value:
        value = 'video'[:max_len]
    stem = value.split('.', 1)[0].rstrip(' ').upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', stem):
        value = ('_' + value)[:max_len].rstrip(' .')
    return value or '_'


def format_size(value):
    try:
        amount = float(value)
        if not math.isfinite(amount) or amount < 0:
            return '-'
    except (TypeError, ValueError, OverflowError):
        return '-'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if amount < 1024:
            return f'{amount:.1f} {unit}'
        amount /= 1024
    return f'{amount:.1f} TB'


def build_output_template(download_dir, random_name=False, limit_length=False, use_folder=False, token=None):
    """任务 token 固定，暂停再继续复用同一模板和 .part/.ytdl 文件。"""
    token = str(token or uuid.uuid4().hex[:12])
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', token):
        raise ValueError('任务标识格式无效')
    directory = os.fspath(download_dir).replace('%', '%%')
    title = '' if random_name else ('%(title).50s_' if limit_length else '%(title).120s_')
    base = title + token
    return os.path.join(directory, base, 'video.%(ext)s') if use_folder else os.path.join(directory, base + '.%(ext)s')
