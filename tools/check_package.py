#!/usr/bin/env python3
"""Inspect distribution contents, then smoke-test the wheel without optional deps."""

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import venv
import zipfile


VIDEO_SUFFIXES = {
    '.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.mpeg', '.mpg', '.ts', '.mts', '.m2ts',
    '.flv', '.f4v', '.wmv', '.asf', '.vob', '.ogv', '.ogm', '.3gp', '.3g2', '.mxf', '.m1v',
    '.m2v', '.divx', '.rm', '.rmvb', '.qt', '.y4m', '.h264', '.h265', '.hevc', '.av1',
}
PRIVATE_DIRECTORIES = {'.agent', '.local', '.omx', '.git', '.venv', 'venv', 'uploads', 'video-samples', 'logs', '__pycache__'}
PRIVATE_SUFFIXES = {'.db', '.safetensors', '.onnx', '.pt', '.pth', '.gguf', '.traineddata', '.pyc'}
DECORATIVE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.otf', '.ttf', '.ttc'}
PRIVATE_NAMES = {'course-images.json', 'custom-themes.json', 'theme-library.json', 'player-standings.txt', 'player-standings.md', 'obs-connection.json', 'external-vision.json', 'capture.json', 'imports.json', 'team-standings.txt', 'team-standings.md'}


def check_member(name, header=b''):
    """Reject private/generated data and videos, even with uppercase or renamed files."""
    path = PurePosixPath(name.lower())
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe archive path: {name}')
    if path.suffix in DECORATIVE_SUFFIXES:
        raise ValueError(f'Decorative image/font in clean distribution: {name}')
    if any(part in PRIVATE_DIRECTORIES or part.startswith('.omx') for part in path.parts):
        raise ValueError(f'Runtime directory in archive: {name}')
    if (path.name in PRIVATE_NAMES or path.name.startswith(('.env', '.obs-connection-', '.external-vision-', '.themes-', '.course-images-', '.overlay-backgrounds-', 'overlay-profiles.json'))
            or path.name.startswith(('custom-themes', 'course-images', 'overlay-backgrounds')) and path.suffix == '.json'
            or '.sqlite' in path.name or '.db-' in path.name or '.db.' in path.name
            or path.suffix in PRIVATE_SUFFIXES | VIDEO_SUFFIXES):
        raise ValueError(f'Private data or media in archive: {name}')
    if (header.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'OTTO', b'ttcf'))
            or header[:4] == b'RIFF' and header[8:12] == b'WEBP'):
        raise ValueError(f'Decorative image/font signature in clean distribution: {name}')
    if (header[4:8] == b'ftyp' or header.startswith(b'\x1aE\xdf\xa3')
            or header[:4] == b'RIFF' and header[8:12] == b'AVI '
            or header.startswith(b'SQLite format 3\x00')):
        raise ValueError(f'Video or database signature in archive: {name}')


def inspect_archive(archive):
    names = set()
    if archive.suffix == '.whl':
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                with source.open(entry) as stream:
                    check_member(entry.filename, stream.read(32))
                names.add(entry.filename)
    else:
        with tarfile.open(archive, 'r:gz') as source:
            for entry in source:
                check_member(entry.name)
                if entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
                    raise ValueError(f'Unexpected archive entry: {entry.name}')
                if entry.isfile():
                    with source.extractfile(entry) as stream:
                        check_member(entry.name, stream.read(32))
                # Source archives have one distribution/version prefix.
                names.add('/'.join(PurePosixPath(entry.name).parts[1:]))
    return names


def check_contents(wheel, source, root):
    wheel_names = inspect_archive(wheel)
    source_names = inspect_archive(source)
    assets = {path.relative_to(root).as_posix() for directory in ('static', 'profiles')
              for path in (root / 'mkw_tracker' / directory).rglob('*') if path.is_file()}
    for label, names in [('wheel', wheel_names), ('sdist', source_names)]:
        missing = assets - names
        if missing:
            raise ValueError(f'{label} missing bundled assets: {sorted(missing)}')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    guides = set(re.findall(r'(?:\]\(|(?:href|src)=["\'])(docs/[^)"\'#]+)', readme))
    # These documented synthetic screenshots ship only with the website/repository.
    # Application archives remain image-free; all other linked docs are required.
    guides -= {'docs/images/standings.png', 'docs/images/overlay.png'}
    missing = {'README.md', 'DESIGN.md', 'ATTRIBUTION.md', *guides} - source_names
    if missing:
        raise ValueError(f'sdist missing documentation: {sorted(missing)}')
    # The grant, exception and bundled notices must travel with both formats.
    # Setuptools 68 uses dist-info/<basename>; newer versions retain directories
    # under dist-info/licenses/. Accept either layout, but require exact content.
    legal_files = {'LICENSE', 'COPYING.md', 'ATTRIBUTION.md', 'THIRD_PARTY_NOTICES.md'}
    legal_files.update(path.relative_to(root).as_posix() for path in (root / 'LICENSES').glob('*.txt'))
    missing = legal_files - source_names
    if missing:
        raise ValueError(f'sdist missing license files: {sorted(missing)}')
    with zipfile.ZipFile(wheel) as wheel_archive, tarfile.open(source, 'r:gz') as source_archive:
        source_members = {'/'.join(PurePosixPath(entry.name).parts[1:]): entry
                          for entry in source_archive if entry.isfile()}
        for name in sorted(legal_files):
            candidates = [entry for entry in wheel_names
                          if PurePosixPath(entry).parts[0].endswith('.dist-info')
                          and '/'.join(PurePosixPath(entry).parts[1:])
                          in {f'licenses/{name}', PurePosixPath(name).name}]
            if len(candidates) != 1:
                raise ValueError(f'wheel missing or ambiguous license file: {name}')
            expected = (root / name).read_bytes()
            with source_archive.extractfile(source_members[name]) as stream:
                if stream.read() != expected:
                    raise ValueError(f'sdist license text differs from source: {name}')
            if wheel_archive.read(candidates[0]) != expected:
                raise ValueError(f'wheel license text differs from source: {name}')


SMOKE = '''
import importlib.util
from importlib.resources import files
from pathlib import Path
import tempfile
import mkw_tracker
from mkw_tracker.store import TrackerStore

assert Path(mkw_tracker.__file__).is_relative_to(Path(__import__('sys').prefix))
for optional in ('cv2', 'numpy', 'PIL', 'websocket', 'playwright'):
    assert importlib.util.find_spec(optional) is None, optional
assert files('mkw_tracker').joinpath('static/index.html').is_file()
assert files('mkw_tracker').joinpath('static/overlay.html').is_file()
with tempfile.TemporaryDirectory() as temporary:
    store = TrackerStore(Path(temporary) / 'scores.sqlite')
    try:
        players = [store.add_player(f'Player {i}')['id'] for i in range(12)]
        store.create_race('smoke', 'Rainbow Road')
        store.submit_result('smoke', players)
        assert sum(player['points'] for player in store.snapshot()['players']) == 82
    finally:
        store.close()
print('Dependency-free installed-wheel smoke passed: 12 players, 82 points')
'''


def smoke_wheel(wheel):
    with tempfile.TemporaryDirectory(prefix='mkw-wheel-') as temporary:
        directory = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(directory / 'env')
        python = directory / 'env' / 'bin' / 'python'
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-index', str(wheel.resolve())],
                       cwd=directory, check=True)
        subprocess.run([str(python), '-I', '-W', 'error::ResourceWarning', '-c', SMOKE], cwd=directory, check=True)
        subprocess.run([str(python), '-I', '-m', 'mkw_tracker', '--help'], cwd=directory, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dist', type=Path)
    args = parser.parse_args()
    wheels = list(args.dist.glob('*.whl'))
    sources = list(args.dist.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error('Expected exactly one wheel and one source archive in dist')
    check_contents(wheels[0], sources[0], Path(__file__).resolve().parents[1])
    smoke_wheel(wheels[0])
    print('Package contents and linked documentation checks passed')


if __name__ == '__main__':
    main()
