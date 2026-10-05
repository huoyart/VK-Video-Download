"""Build and verify a portable Windows GUI release with PyInstaller."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
PRODUCT = 'VK-Video-Downloader-2.2-win64'
EXE_NAME = PRODUCT + '.exe'
ZIP_NAME = PRODUCT + '.zip'
SUMS_NAME = PRODUCT + '-SHA256SUMS.txt'
RELEASE = ROOT / 'release'


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def gui_subsystem(path: Path) -> int:
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Not a Windows executable')
        stream.seek(0x3c)
        pe_offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('Invalid PE header')
        stream.seek(pe_offset + 24 + 68)
        return struct.unpack('<H', stream.read(2))[0]


def verify() -> None:
    exe = RELEASE / EXE_NAME
    archive = RELEASE / ZIP_NAME
    sums = RELEASE / SUMS_NAME
    for path in (exe, archive, sums):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
    subsystem = gui_subsystem(exe)
    if subsystem != 2:
        raise ValueError(f'Expected Windows GUI subsystem 2; found {subsystem}')
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise ValueError('ZIP integrity test failed')
        if set(zf.namelist()) != {EXE_NAME, 'README.md', 'UPDATE_REPORT.md', 'RELEASE_NOTES.md'}:
            raise ValueError('Unexpected release ZIP contents')
        if hashlib.sha256(zf.read(EXE_NAME)).hexdigest() != checksum(exe):
            raise ValueError('EXE differs from ZIP member')
        for name in ('README.md', 'UPDATE_REPORT.md', 'RELEASE_NOTES.md'):
            if zf.read(name) != (ROOT / name).read_bytes():
                raise ValueError(f'{name} differs from ZIP member')
    expected = f'{checksum(exe)}  {EXE_NAME}\n{checksum(archive)}  {ZIP_NAME}\n'
    if sums.read_text(encoding='ascii') != expected:
        raise ValueError(f'{SUMS_NAME} differs from release files')
    print(f'VERIFIED_GUI_SUBSYSTEM={subsystem}')
    print('VERIFIED_ZIP=OK')
    print(f'EXE_SHA256={checksum(exe)}')
    print(f'ZIP_SHA256={checksum(archive)}')


def build() -> None:
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('Build requires 64-bit Windows Python')
    icon = ROOT / 'theme' / 'icon.ico'
    if not icon.is_file():
        raise FileNotFoundError(icon)
    importlib.metadata.version('pyinstaller')
    dist = ROOT / 'dist'
    work = ROOT / 'build'
    for target in (dist, work, RELEASE):
        if not target.resolve().is_relative_to(ROOT):
            raise ValueError(f'Build path leaves project: {target}')
        target.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile',
               '--windowed', '--name', PRODUCT, '--icon', str(icon),
               '--add-data', f'{icon}{os.pathsep}theme', '--collect-all', 'yt_dlp',
               '--distpath', str(dist), '--workpath', str(work),
               '--specpath', str(work), str(ROOT / 'vk_video_download.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    source = dist / EXE_NAME
    if gui_subsystem(source) != 2:
        raise ValueError('PyInstaller did not produce a windowed executable')
    exe = RELEASE / EXE_NAME
    shutil.copy2(source, exe)
    archive = RELEASE / ZIP_NAME
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for path in (exe, ROOT / 'README.md', ROOT / 'UPDATE_REPORT.md', ROOT / 'RELEASE_NOTES.md'):
            zf.write(path, arcname=path.name)
    sums = RELEASE / SUMS_NAME
    sums.write_text(f'{checksum(exe)}  {EXE_NAME}\n{checksum(archive)}  {ZIP_NAME}\n',
                    encoding='ascii')
    verify()
    print(f'EXE_PATH={exe}')
    print(f'ZIP_PATH={archive}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true', help='check an existing release without rebuilding')
    args = parser.parse_args()
    if args.verify_only:
        verify()
    else:
        build()
