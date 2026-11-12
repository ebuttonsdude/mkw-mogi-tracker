"""Setup-owned reader preferences, separate from running application settings."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys

from .name_engines import GGUF_PROFILE, NAME_ENGINES as ALL_NAME_ENGINES, QWEN_PROFILES

# External servers have no tracker-managed install/remove/default lifecycle.
NAME_ENGINES = ALL_NAME_ENGINES - {'external-vision'}


def assets(engine):
    if engine == 'paddle':
        return ('paddle-venv', 'models/paddle-ppocrv5')
    if engine == GGUF_PROFILE['engine']:
        return ('models/' + GGUF_PROFILE['model_dir'],)
    profile = QWEN_PROFILES[engine]
    return (profile['environment'], 'models/' + profile['model_dir'])


def read(root):
    try:
        path = Path(root) / '.local/reader-setup.json'
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('Reader setup paths must not be symlinks')
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {'default': 'auto', 'removed': []}
    if (not isinstance(value, dict) or not isinstance(value.get('default'), str) or value.get('default') not in NAME_ENGINES
            or not isinstance(value.get('removed'), list)
            or any(not isinstance(e, str) or e not in NAME_ENGINES - {'auto', 'tesseract'} for e in value['removed'])
            or value['default'] in value['removed']):
        raise ValueError('Invalid reader setup preferences')
    return value


def write(root, value):
    directory = Path(root) / '.local'
    if any(path.is_symlink() for path in (directory, *directory.parents)) or (directory / 'reader-setup.json').is_symlink():
        raise ValueError('Reader setup paths must not be symlinks')
    directory.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix='.reader-setup-')
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output)
        os.replace(name, directory / 'reader-setup.json')
    finally:
        Path(name).unlink(missing_ok=True)


def available_files(root, engine):
    local = Path(root) / '.local'
    if engine == GGUF_PROFILE['engine']:
        model = local / 'models' / GGUF_PROFILE['model_dir']
        try:
            config = json.loads((model / 'runtime.json').read_text())
            executable = Path(config['executable'])
            return (executable.is_file() and
                    all((model / GGUF_PROFILE[key]).is_file() for key in ('model_file', 'projector_file')))
        except (OSError, ValueError, KeyError, TypeError):
            return False
    environment, model_path = assets(engine)
    python = local / environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    model = local / model_path
    if engine == 'paddle':
        return python.is_file() and (model / 'inference.json').is_file() and (model / 'inference.pdiparams').is_file()
    if not python.is_file() or not (model / 'config.json').is_file():
        return False
    index = model / 'model.safetensors.index.json'
    if index.is_file():
        try:
            mapping = json.loads(index.read_text())['weight_map']
            return (isinstance(mapping, dict) and bool(mapping)
                    and all(isinstance(name, str) and Path(name).name == name and (model / name).is_file()
                            for name in mapping.values()))
        except (OSError, ValueError, KeyError, TypeError):
            return False
    return (model / 'model.safetensors').is_file()


def installed(root, engine):
    if engine not in NAME_ENGINES:
        return False
    if engine in {'auto', 'tesseract'}:
        return True
    return engine not in read(root)['removed'] and available_files(root, engine)


def public_preferences(root):
    try:
        value = read(root)
        return {'default': value['default'], 'warning': None}
    except (OSError, ValueError, TypeError):
        return {'default': 'tesseract', 'warning': 'Reader setup preferences are invalid. Restore .local/reader-setup.json from a known-good backup, or manually correct its default and removed fields while preserving removed-reader entries. Setup will not overwrite this file.'}


def set_default(root, engine):
    if engine not in NAME_ENGINES or not installed(root, engine):
        raise ValueError('Choose an installed reader')
    value = read(root)
    value['default'] = engine
    write(root, value)


def enable(root, engine):
    value = read(root)
    if engine in value['removed']:
        value['removed'].remove(engine)
        write(root, value)


def remove(root, engine, replacement=None):
    if engine not in NAME_ENGINES - {'auto', 'tesseract'}:
        raise ValueError('Choose a removable neural reader')
    value = read(root)
    if value['default'] == engine:
        if replacement == engine or replacement not in NAME_ENGINES or not installed(root, replacement):
            raise ValueError('Choose an installed replacement before removing the default')
        value['default'] = replacement
    others = NAME_ENGINES - {'auto', 'tesseract', engine}
    # Only complete, enabled profiles own shared assets. An abandoned/partial
    # environment alone is not an installation; rerun its installer to repair it.
    retained = {p for other in others if installed(root, other) for p in assets(other)}
    targets = [Path(root) / '.local' / p for p in assets(engine) if p not in retained]
    for target in targets:
        for part in (target, *target.parents):
            if part.is_symlink():
                raise ValueError('Refusing symlink reader assets')
    value['removed'] = sorted(set(value['removed']) | {engine})
    write(root, value)
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
