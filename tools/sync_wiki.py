#!/usr/bin/env python3
"""Reconcile generated pages into a wiki checkout; never run Git or use the network."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_wiki import MARKER_CONTENT, OUTPUT_MARKER  # noqa: E402

MANIFEST = '.mkw-wiki-manifest.json'
PAGE_NAME = re.compile(r'(?:Home|_Sidebar|[a-z0-9]+(?:-[a-z0-9]+)*)\.md')
DIGEST = re.compile(r'[0-9a-f]{64}')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected a regular wiki file, not a symlink: {path.name}')
    return path.read_bytes()


def read_manifest(destination):
    path = destination / MANIFEST
    if not path.exists() and not path.is_symlink():
        return None
    manifest = json.loads(checked_file(path))
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'files'}
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1):
        raise ValueError('Unsupported wiki ownership manifest.')
    files = manifest['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('Invalid wiki ownership file inventory.')
    for name, checksum in files.items():
        if not PAGE_NAME.fullmatch(name) or not isinstance(checksum, str) or not DIGEST.fullmatch(checksum):
            raise ValueError('Unsafe filename or checksum in wiki ownership manifest.')
    return files


def write_atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.wiki-sync-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def sync_wiki(source, destination):
    """Preserve unowned files; old generated files must still match saved hashes."""
    paths = (source, *source.parents, destination, *destination.parents)
    if any(path.is_symlink() for path in paths) or not source.is_dir() or not destination.is_dir():
        raise ValueError('Source and wiki checkout must be existing directories, not symlinks.')
    if source.resolve() == destination.resolve():
        raise ValueError('Generate pages separately from the wiki checkout.')
    if checked_file(source / OUTPUT_MARKER).decode('utf-8') != MARKER_CONTENT:
        raise ValueError('Source is not an owned wiki export.')
    generated = {}
    for path in source.iterdir():
        if path.name == OUTPUT_MARKER:
            continue
        if not PAGE_NAME.fullmatch(path.name):
            raise ValueError(f'Unexpected generated wiki file: {path.name}')
        generated[path.name] = checked_file(path)
    if not {'Home.md', '_Sidebar.md'} <= generated.keys():
        raise ValueError('Generated wiki requires Home.md and _Sidebar.md.')
    previous = read_manifest(destination)
    owned = previous or {}
    # Preflight everything before writing/deleting any page. A manual change to
    # an owned page is a conflict, not permission to erase unrelated content.
    for name, checksum in owned.items():
        path = destination / name
        if path.exists() or path.is_symlink():
            if digest(checked_file(path)) != checksum:
                raise ValueError(f'Previously generated wiki page was edited: {name}')
    for name in generated:
        path = destination / name
        if path.exists() or path.is_symlink():
            checked_file(path)
            if name not in owned and not (previous is None and name == 'Home.md'):
                raise ValueError(f'Refusing to overwrite an unowned wiki page: {name}')
    changed = []
    removed = []
    for name, data in generated.items():
        path = destination / name
        if not path.exists() or path.read_bytes() != data:
            write_atomic(path, data)
            changed.append(name)
    for name in owned.keys() - generated.keys():
        path = destination / name
        if path.exists():
            path.unlink()
            removed.append(name)
    manifest = {'schema_version': 1, 'files': {name: digest(data) for name, data in sorted(generated.items())}}
    write_atomic(destination / MANIFEST, (json.dumps(manifest, indent=2) + '\n').encode('utf-8'))
    return sorted(changed), sorted(removed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        changed, removed = sync_wiki(args.source, args.destination)
    except (OSError, ValueError) as error:
        print(f'Wiki sync failed: {error}', file=sys.stderr)
        return 1
    print(f'Wiki checkout reconciled: {len(changed)} changed pages, {len(removed)} removed generated pages.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
