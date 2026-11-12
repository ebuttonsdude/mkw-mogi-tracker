#!/usr/bin/env python3
"""Install the pinned Qwen GGUF reader and a local CPU or Vulkan llama.cpp runtime."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mkw_tracker.reader_setup import enable as enable_reader, read as read_reader_setup  # noqa: E402
from mkw_tracker.name_engines import GGUF_PROFILE  # noqa: E402

VERSION = 'b12110'
RUNTIMES = {
    ('win32', 'vulkan'): ('llama-b12110-bin-win-vulkan-x64.zip', '9732a5e48887ca54d0bfe4af4324dc85fd56cd8cda19b52df3542fa6ebe6874f'),
    ('linux', 'vulkan'): ('llama-b12110-bin-ubuntu-vulkan-x64.tar.gz', 'a5d741940956ec62e8783aee7e5a4c7c761af867d6f027c3277a51e1a3ac59b0'),
    ('win32', 'cpu'): ('llama-b12110-bin-win-cpu-x64.zip', '98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2'),
    ('linux', 'cpu'): ('llama-b12110-bin-ubuntu-x64.tar.gz', '368227edcc229be5f9bed16a2223d79b5e41d0ac2e19bcf5825e82291c060e55'),
}
LLAMA_LICENSE = (
    'https://raw.githubusercontent.com/ggml-org/llama.cpp/b12110/LICENSE',
    '94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d',
)
# The pinned GGUF repository declares Apache-2.0 but ships no LICENSE file.
# Preserve the license text from the corresponding official Qwen3-VL project.
MODEL_LICENSE = (
    'https://raw.githubusercontent.com/QwenLM/Qwen3-VL/96588727e44c78b25ba03ea03b8e12f7e64fd0da/LICENSE',
    'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
)
MAX_DOWNLOAD_BYTES = 8 * 1024**3
MAX_EXTRACT_BYTES = 4 * 1024**3


def platform_check():
    if sys.platform not in {'linux', 'win32'} or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise ValueError('GGUF setup supports x64 Windows and Linux only; ARM and macOS are not supported.')


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, target, expected):
    """Reuse verified files; failed or interrupted downloads never replace the target."""
    if target.is_symlink():
        raise ValueError(f'Refusing symlink download target: {target.name}')
    if target.is_file() and checksum(target) == expected:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + '.', suffix='.part', delete=False) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            total = 0
            request = urllib.request.Request(url, headers={'User-Agent': 'mkw-tracker-setup'})
            with urllib.request.urlopen(request, timeout=120) as response:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError('Download exceeds size limit')
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f'Checksum mismatch for {target.name}; existing file preserved. Retry setup.')
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def archive_path(name):
    if not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Unsafe archive path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Unsafe archive path')
    return path.as_posix()


def link_target(name, target, hardlink=False):
    if not target or '\\' in target or ':' in target or '\x00' in target or target.startswith('/'):
        raise ValueError('Unsafe archive link')
    parts = [] if hardlink else list(PurePosixPath(name).parent.parts)
    for part in PurePosixPath(target).parts:
        if part == '..':
            if not parts:
                raise ValueError('Archive link escapes its root')
            parts.pop()
        elif part != '.':
            parts.append(part)
    if not parts:
        raise ValueError('Archive link has no file target')
    return '/'.join(parts)


def extract_runtime(archive, destination):
    """Extract into a fresh directory; internal tar links become regular copies."""
    if destination.exists():
        raise ValueError('Extraction destination must be new')
    destination.mkdir()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            entries = {}
            total = 0
            for item in source.infolist():
                name = archive_path(item.filename)
                if name in entries or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError('Duplicate archive entry or unsupported ZIP link')
                entries[name] = item
                total += item.file_size
            if len(entries) > 10000 or total > MAX_EXTRACT_BYTES:
                raise ValueError('Archive exceeds extraction limit')
            for name, item in entries.items():
                target = destination / name
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(item) as incoming, target.open('xb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        return
    with tarfile.open(archive, 'r:gz') as source:
        entries = {}
        for item in source:
            name = archive_path(item.name)
            if name in entries or not (item.isfile() or item.isdir() or item.issym() or item.islnk()):
                raise ValueError('Duplicate or unsupported archive entry')
            entries[name] = item
            if len(entries) > 10000:
                raise ValueError('Archive exceeds extraction limit')

        def regular(name, seen):
            if name in seen or name not in entries or len(seen) > 100:
                raise ValueError('Archive link is cyclic or has a missing target')
            item = entries[name]
            if item.issym() or item.islnk():
                return regular(link_target(name, item.linkname, item.islnk()), seen | {name})
            if not item.isfile():
                raise ValueError('Archive link must target a regular file')
            return item

        files = {name: regular(name, set()) for name, item in entries.items() if not item.isdir()}
        if sum(item.size for item in files.values()) > MAX_EXTRACT_BYTES:
            raise ValueError('Archive exceeds extraction limit')
        for name, item in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(item) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            target.chmod(item.mode & 0o777)



def reconcile_runtime(staging, installed):
    """Repair only pinned archive files, without deleting unknown files or following links."""
    if installed.is_symlink() or (installed.exists() and not installed.is_dir()):
        raise ValueError('Refusing unsafe runtime directory')
    files = [path for path in staging.rglob('*') if path.is_file()]
    # Validate every destination before making repairs. Never write through an
    # existing symlink, special file, or directory where a regular file belongs.
    for source in files:
        relative = source.relative_to(staging)
        parent = installed
        for part in relative.parts[:-1]:
            parent = parent / part
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError('Unsafe existing runtime directory entry')
        target = installed / relative
        if target.is_symlink() or (target.exists() and not stat.S_ISREG(target.stat().st_mode)):
            raise ValueError('Unsafe existing runtime file entry')
    for source in files:
        target = installed / source.relative_to(staging)
        mode = source.stat().st_mode & 0o777
        if target.is_file() and checksum(target) == checksum(source) and target.stat().st_mode & 0o777 == mode:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + '.', suffix='.repair', delete=False) as output:
                temporary = Path(output.name)
                with source.open('rb') as incoming:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
            temporary.chmod(mode)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def install(backend='cpu', dry_run=False):
    read_reader_setup(ROOT)
    platform_check()
    filename, digest = RUNTIMES[(sys.platform, backend)]
    archive_url = f'https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{filename}'
    model = ROOT / '.local/models' / GGUF_PROFILE['model_dir']
    runtime_root = ROOT / '.local/llama.cpp'
    installed = runtime_root / f'{VERSION}-{sys.platform}-{backend}'
    archive = ROOT / '.local/downloads' / filename
    files = [(GGUF_PROFILE[key + '_file'], GGUF_PROFILE[key + '_sha256']) for key in ('model', 'projector')]
    base = f'https://huggingface.co/{GGUF_PROFILE["model_id"]}/resolve/{GGUF_PROFILE["revision"]}'
    print(f'{GGUF_PROFILE["label"]}: {backend.upper()} backend; about 3.33 GB of model/projector files plus runtime.')
    if sys.platform == 'linux':
        print('Linux binaries need modern glibc/libstdc++, OpenSSL 3 and libgomp. Vulkan also needs libvulkan and a vendor driver.')
    if dry_run:
        print(f'Preview only: download {archive_url}')
        for name, _ in files:
            print(f'Preview only: download {base}/{name}')
        print(f'Preview only: download license {LLAMA_LICENSE[0]}')
        print(f'Preview only: download license {MODEL_LICENSE[0]}')
        print(f'After successful installation, write backend configuration to {model / "runtime.json"}')
        return
    download(archive_url, archive, digest)
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='install-', dir=runtime_root) as temporary:
        staging = Path(temporary) / 'runtime'
        extract_runtime(archive, staging)
        executable_name = 'llama-server.exe' if sys.platform == 'win32' else 'llama-server'
        if len(list(staging.rglob(executable_name))) != 1:
            raise ValueError('Runtime archive must contain exactly one llama-server executable')
        if installed.exists() or installed.is_symlink():
            reconcile_runtime(staging, installed)
        else:
            staging.rename(installed)
    executable_name = 'llama-server.exe' if sys.platform == 'win32' else 'llama-server'
    candidates = list(installed.rglob(executable_name))
    if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].is_symlink():
        raise ValueError('Installed runtime is incomplete; preserve it and report this error')
    executable = candidates[0].resolve()
    if not executable.is_relative_to(installed.resolve()):
        raise ValueError('Runtime executable is outside its installation')
    for name, expected in files:
        print(f'Checking/downloading {name} ...')
        download(f'{base}/{name}', model / name, expected)
    download(LLAMA_LICENSE[0], installed / 'LICENSE-llama.cpp', LLAMA_LICENSE[1])
    download(MODEL_LICENSE[0], model / 'LICENSE-Qwen3-VL', MODEL_LICENSE[1])
    config = model / 'runtime.json'
    if config.is_symlink():
        raise ValueError('Refusing symlink runtime configuration')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=model, prefix='runtime-', suffix='.json', delete=False) as output:
            temporary = Path(output.name)
            json.dump({'executable': str(executable), 'backend': backend}, output)
            output.write('\n')
        os.replace(temporary, config)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    enable_reader(ROOT, GGUF_PROFILE['engine'])
    print(f'{GGUF_PROFILE["engine"]} name recognition ready at {model}')
    print('Select Qwen3-VL-4B GGUF Q4_K_M explicitly in the dashboard. Automatic does not select it.')
    print('CPU can be slow. To change backend, rerun setup explicitly; no runtime downloads or silent fallback occur.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('cpu', 'vulkan'), default='cpu')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        install(args.backend, args.dry_run)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f'GGUF setup failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
