#!/usr/bin/env python3
"""Guided local setup: capture, reports, and an optional name reader."""

import argparse
from pathlib import Path
import shlex
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mkw_tracker.name_engines import GGUF_PROFILE, QWEN_PROFILES  # noqa: E402
from mkw_tracker import reader_setup  # noqa: E402
from mkw_tracker.tesseract import tesseract_executable  # noqa: E402

# Keep the original four menu numbers and command-line aliases stable.
READERS = {
    'cpu': 'paddle', '8gb': 'qwen-4b-nf4', 'nvfp4': 'qwen-nvfp4', 'skip': None,
    **{engine: engine for engine in QWEN_PROFILES if engine not in {'qwen-4b-nf4', 'qwen-nvfp4'}},
    GGUF_PROFILE['engine']: GGUF_PROFILE['engine'],
}
READER_ALIASES = {'paddle': 'cpu', 'qwen-4b-nf4': '8gb', 'qwen-nvfp4': 'nvfp4', 'tesseract': 'skip'}


def reader_description(choice):
    choice = READER_ALIASES.get(choice, choice)
    if choice == 'recommended':
        return ('Recommended bundle: PaddleOCR + Qwen3-VL-4B GGUF Q4_K_M. '
                'Install both; choose CPU or Vulkan for Qwen. Setup offers Qwen as the default only after success. '
                'PaddleOCR stays separately selectable, not a silent fallback.')
    if choice == 'cpu':
        return 'CPU: PaddleOCR. No NVIDIA GPU needed. About 81 MB of model files.'
    if choice == 'skip':
        return 'Skip for now: keep basic Tesseract recognition; add a reader later.'
    if choice == GGUF_PROFILE['engine']:
        return (f'Recommended Qwen: {GGUF_PROFILE["label"]}. CPU or Vulkan GPU (AMD/Intel/NVIDIA), no CUDA required. '
                'x64 Windows/Linux only. About 3.33 GB of model/projector files, plus runtime. '
                'Broader GPU compatibility and a smaller download than the NF4 setup. '
                'CPU can be slow; Vulkan needs a compatible driver. A small paired check found similar quality, '
                'not proof of broad NF4 equivalence.')
    profile = QWEN_PROFILES[READERS[choice]]
    memory = f'Needs {profile["min_free_gib"]} GiB FREE VRAM.'
    if choice == '8gb':
        return (f'8 GiB NVIDIA GPU: {profile["label"]} (CUDA alternative). {memory} '
                'About 8.88 GB of BF16 source weights, quantized when loaded. '
                'An 8 GiB card is not a guarantee of enough free memory.')
    if profile['requires_nvfp4']:
        download = 'About 17.5 GB of model files. ' if choice == 'nvfp4' else 'Very large model download. '
        unavailable = ' Unavailable on this platform.' if sys.platform != 'linux' else ''
        return (f'RTX 50-series: {profile["label"]}. Linux only, NVIDIA Blackwell SM120/121, CUDA 13. '
                f'{memory} {download}Not every RTX 50-series card has enough memory. '
                f'WSL2 is not yet validated.{unavailable}')
    return (f'Advanced NVIDIA GPU: {profile["label"]}. Requires NVIDIA CUDA. {memory} '
            'About 17.5 GB of model files.')


def choose_reader():
    print('\nA name reader is optional, but highly recommended for captured player names.')
    print('Choose a reader, the Recommended bundle, or skip. Model sizes below exclude Python packages and download caches.\n')
    choices = [*READERS, 'recommended']
    print(f'Recommended Qwen starting point: {choices.index(GGUF_PROFILE["engine"]) + 1}. '
          f'{GGUF_PROFILE["label"]} — broader compatibility and a smaller download.\n'
          'PaddleOCR remains the smallest CPU download. No choice is selected automatically.\n')
    for number, choice in enumerate(choices, 1):
        print(f'  {number}. [{READERS.get(choice, choice) or "tesseract"}] {reader_description(choice)}\n')
    print('CUDA readers check CUDA compatibility, not free VRAM at race time. GGUF uses CPU or Vulkan instead.')
    while True:
        answer = input(f'Your choice [1-{len(choices)}; no default]: ').strip().lower()
        answer = READER_ALIASES.get(answer, answer)
        if answer in choices:
            return answer
        if answer in {str(number) for number in range(1, len(choices) + 1)}:
            return choices[int(answer) - 1]
        print(f'Enter a number from 1 to {len(choices)}, or a reader ID. Nothing has been installed yet.')


def choose_gguf_backend():
    print('\nGGUF backend: 1. Vulkan GPU (AMD/Intel/NVIDIA with a compatible driver); 2. CPU (slower).')
    while True:
        answer = input('Backend [1-2; no default]: ').strip().lower()
        if answer in {'1', 'vulkan'}:
            return 'vulkan'
        if answer in {'2', 'cpu'}:
            return 'cpu'
        print('Enter 1 for Vulkan or 2 for CPU. Nothing has been installed yet.')


def prerequisites(reader, manual_only):
    missing = []
    if sys.version_info < (3, 11):
        missing.append('Python 3.11 or newer is required. Use Python 3.12 for the guided setup.')
    if not manual_only:
        engine = GGUF_PROFILE['engine'] if reader == 'recommended' else READERS[reader]
        if engine in QWEN_PROFILES and QWEN_PROFILES[engine]['requires_nvfp4'] and sys.platform != 'linux':
            missing.append('NVFP4 requires Linux Python; native Windows and macOS are unsupported. '
                           'Choose CPU or the 8 GiB NVIDIA reader instead.')
        if reader in {GGUF_PROFILE['engine'], 'recommended'} and (sys.platform not in {'linux', 'win32'} or
                                                platform.machine().lower() not in {'amd64', 'x86_64'}):
            missing.append('GGUF setup supports x64 Windows and Linux only; ARM and macOS are unsupported.')
        if not tesseract_executable():
            missing.append('Tesseract OCR was not found on PATH or in the usual Windows installation folders. '
                           'Install it using your platform guide, '
                           'then reopen the terminal. Windows: add the Tesseract installation folder to PATH.')
        if reader not in {'skip', GGUF_PROFILE['engine']} and not shutil.which('uv'):
            missing.append('uv is required for the separate name-reader environment. Install uv, '
                           'reopen the terminal, then rerun setup; or choose --reader skip.')
    return missing


def setup_commands(reader, manual_only, gguf_backend='cpu'):
    python = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if manual_only:
        if python.is_file():
            return []
        uv = shutil.which('uv')
        return [[uv, 'venv', '--python', sys.executable, str(ROOT / '.venv')]] if uv else [
            [sys.executable, '-m', 'venv', str(ROOT / '.venv')]]
    commands = [[sys.executable, str(ROOT / 'tools/setup_local.py')]]
    if reader == 'recommended':
        commands.extend([
            [str(python), str(ROOT / 'tools/setup_neural.py'), '--engine', 'paddle'],
            [str(python), str(ROOT / 'tools/setup_neural.py'), '--engine', GGUF_PROFILE['engine'],
             '--gguf-backend', gguf_backend],
        ])
    elif READERS[reader]:
        commands.append([str(python), str(ROOT / 'tools/setup_neural.py'), '--engine', READERS[reader]])
        if reader in {GGUF_PROFILE['engine'], 'recommended'}:
            commands[-1] += ['--gguf-backend', gguf_backend]
    return commands


def prerequisite_commands():
    if sys.platform != 'linux':
        return []
    try:
        distro = platform.freedesktop_os_release().get('ID')
    except OSError:
        return []
    packages = []
    if not tesseract_executable():
        packages += {'arch': ['tesseract', 'tesseract-data-eng', 'noto-fonts-cjk'],
                     'fedora': ['tesseract', 'google-noto-sans-cjk-fonts'],
                     'ubuntu': ['tesseract-ocr', 'fonts-noto-cjk']}.get(distro, [])
    if not shutil.which('uv') and distro in {'arch', 'fedora'}:
        packages.append('uv')
    if not packages:
        return []
    prefix = {'arch': ['sudo', 'pacman', '-S', '--needed'],
              'fedora': ['sudo', 'dnf', 'install'],
              'ubuntu': ['sudo', 'apt', 'install']}[distro]
    return ([['sudo', 'apt', 'update']] if distro == 'ubuntu' else []) + [prefix + packages]


def choose_installed(exclude=None, removable=False):
    choices = [engine for engine in sorted(reader_setup.NAME_ENGINES)
               if engine != exclude and (not removable or engine not in {'auto', 'tesseract'})
               and reader_setup.installed(ROOT, engine)]
    for number, engine in enumerate(choices, 1):
        print(f'{number}. {engine}')
    answer = input('Choose installed reader (blank cancels): ').strip()
    if answer in choices:
        return answer
    if answer.isdigit() and 1 <= int(answer) <= len(choices):
        return choices[int(answer) - 1]
    raise ValueError('No replacement selected; nothing changed.')


def choose_action():
    print('1. Install reader / configure capture\n2. Remove reader\n3. Set default reader')
    answer = input('Action [1-3]: ').strip()
    if answer not in {'1', '2', '3'}:
        raise ValueError('Choose action 1, 2 or 3; nothing changed.')
    return {'1': 'install', '2': 'remove', '3': 'default'}[answer]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reader', choices=[*READERS, *READER_ALIASES, 'auto', 'recommended'], help='Explicit reader choice; omit for the guided menu.')
    parser.add_argument('--gguf-backend', choices=('cpu', 'vulkan'), help='GGUF backend; explicit --reader defaults to CPU.')
    parser.add_argument('--manual-only', action='store_true', help='Prepare only manual scoring; no capture/report packages.')
    parser.add_argument('--dry-run', action='store_true', help='Show the plan and missing prerequisites without installing.')
    parser.add_argument('--action', choices=('install', 'remove', 'default'), default=None)
    parser.add_argument('--replacement', choices=sorted(reader_setup.NAME_ENGINES))
    parser.add_argument('--set-default', action='store_true')
    parser.add_argument('--install-prerequisites', action='store_true',
                        help='Explicitly permit listed system packages on recognized Linux distributions.')
    args = parser.parse_args(argv)
    interactive = sys.stdin.isatty() and args.reader is None and not args.manual_only
    try:
        reader_setup.read(ROOT)  # Refuse malformed setup state before any installation or mutation.
        args.action = args.action or (choose_action() if interactive else 'install')
        if interactive and args.action != 'install':
            args.reader = choose_installed(removable=args.action == 'remove')
            engine = READERS.get(READER_ALIASES.get(args.reader, args.reader), args.reader)
            if args.action == 'remove':
                print('Close the tracker before removal. Shared assets are retained; candidate paths:')
                for path in reader_setup.assets(engine):
                    print(ROOT / '.local' / path)
                if reader_setup.read(ROOT)['default'] == engine:
                    args.replacement = choose_installed(exclude=engine)
                if input('Remove this reader? [y/N]: ').strip().lower() != 'y':
                    return 130
    except (EOFError, KeyboardInterrupt):
        print('Setup cancelled. Nothing changed.')
        return 130
    except (OSError, ValueError) as error:
        print(f'Setup stopped: {error}', file=sys.stderr)
        return 1
    if args.action != 'install':
        if args.reader == 'recommended':
            parser.error('Recommended is an install bundle; choose one installed reader for remove/default.')
        engine = READERS.get(READER_ALIASES.get(args.reader, args.reader), args.reader)
        if args.reader in {'skip', 'tesseract'}:
            engine = 'tesseract'
        if not engine:
            parser.error('Choose --reader for management.')
        if args.dry_run:
            print(f'Preview: {args.action} {engine}; replacement {args.replacement}')
            return 0
        try:
            if args.action == 'remove':
                reader_setup.remove(ROOT, engine, args.replacement)
            else:
                reader_setup.set_default(ROOT, engine)
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        return 0
    if args.reader == 'auto':
        parser.error('Automatic is a default-selection policy, not an installable reader; use --action default.')
    if args.manual_only and args.reader:
        parser.error('--manual-only and --reader are separate setup routes; choose one.')
    reader = READER_ALIASES.get(args.reader, args.reader)
    gguf_backend = args.gguf_backend or 'cpu'
    if args.manual_only and args.gguf_backend:
        parser.error('--gguf-backend requires --reader qwen-4b-gguf')
    if not args.manual_only and reader is None:
        if not sys.stdin.isatty():
            parser.error('Choose --reader with a reader ID when not using an interactive terminal; see --help.')
        try:
            reader = choose_reader()
            if reader in {GGUF_PROFILE['engine'], 'recommended'} and args.gguf_backend is None:
                gguf_backend = choose_gguf_backend()
        except (EOFError, KeyboardInterrupt):
            print('\nSetup cancelled. Nothing has been installed.')
            return 130

    if args.gguf_backend and reader not in {GGUF_PROFILE['engine'], 'recommended'}:
        parser.error('--gguf-backend requires the qwen-4b-gguf reader')

    print('\nMKW-Mogi-Tracker setup')
    print('Manual scoring only.' if args.manual_only else 'Install capture and report support, plus basic OCR data.')
    if reader:
        print(reader_description(reader))
        if reader in {GGUF_PROFILE['engine'], 'recommended'}:
            print(f'GGUF backend: {gguf_backend.upper()}')
    print('Everything stays in this checkout (.venv and .local), apart from tool download caches.')
    print('System packages require explicit approval. Saved sessions and OBS settings are not changed.')
    system_commands = prerequisite_commands() if not args.manual_only else []
    if system_commands:
        print('Optional system prerequisites:')
        for command in system_commands:
            print(shlex.join(command))
        approved = args.install_prerequisites
        if interactive and not args.dry_run and not approved:
            try:
                approved = input('Install these system packages? [y/N]: ').strip().lower() == 'y'
            except (EOFError, KeyboardInterrupt):
                print('Setup cancelled before prerequisite installation.')
                return 130
        if approved and not args.dry_run:
            try:
                for command in system_commands:
                    subprocess.run(command, check=True)
            except (OSError, subprocess.CalledProcessError) as error:
                print(f'Prerequisite installation failed: {error}', file=sys.stderr)
                return 1
    missing = prerequisites(reader, args.manual_only)
    if missing and not args.dry_run:
        print('\nSetup stopped before installation:\n- ' + '\n- '.join(missing), file=sys.stderr)
        print('See docs/getting-started.md for your platform prerequisites.', file=sys.stderr)
        return 1
    commands = setup_commands(reader, args.manual_only, gguf_backend)
    if args.dry_run:
        print('\nPreview only: no commands, downloads, or installations will run.')
        if missing:
            print('Needed before installation:\n- ' + '\n- '.join(missing))
        for command in commands:
            # Display only; commands execute as argument lists, never through a shell.
            print(subprocess.list2cmdline(command) if sys.platform == 'win32' else shlex.join(command))
        return 0
    try:
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'\nSetup did not finish: {error}. Review the error above, then rerun '
              'the same setup command. Completed downloads may be reused.', file=sys.stderr)
        return 1

    installed_bundle = reader == 'recommended'
    if installed_bundle:
        # Both installer commands completed. Preserve existing default until explicitly accepted.
        reader = GGUF_PROFILE['engine']
    if reader and (READERS[reader] or args.set_default):
        try:
            if installed_bundle:
                reader_setup.enable(ROOT, 'paddle')
            engine = READERS[reader] or 'tesseract'
            reader_setup.enable(ROOT, READERS[reader])
            change_default = args.set_default
            if interactive and not change_default:
                change_default = input(f'Set {READERS[reader] or "tesseract"} as the default? [y/N]: ').strip().lower() == 'y'
            if change_default:
                reader_setup.set_default(ROOT, engine)
        except (EOFError, KeyboardInterrupt):
            print('Installation finished; default selection cancelled.')
            return 130
        except (OSError, ValueError) as error:
            print(f'Installation finished, but reader preference update failed: {error}', file=sys.stderr)
            return 1
    print('\nSetup complete. Start the tracker from this folder:')
    print(r'Double-click startWindows.bat (or run .\.venv\Scripts\python.exe -m mkw_tracker)' if sys.platform == 'win32'
          else 'sh startLinux.sh')
    print('Open http://127.0.0.1:8765 in your browser. Keep the terminal open while using the tracker.')
    if reader and reader != 'skip':
        label = ('PaddleOCR' if reader == 'cpu' else GGUF_PROFILE['label'] if reader in {GGUF_PROFILE['engine'], 'recommended'}
                 else QWEN_PROFILES[READERS[reader]]['label'])
        print(f'Choose {label} in Capture & review → Name recognition, or Videos → Name reader.')
        if reader in {GGUF_PROFILE['engine'], 'recommended'}:
            print('Automatic does not select this reader. CPU inference can take longer than GPU inference.')
        elif reader != 'cpu':
            if QWEN_PROFILES[READERS[reader]]['precision'] in {'nf4', 'nvfp4'}:
                print('Automatic does not select this GPU reader.')
            print('Select your installed reader explicitly. Leave free VRAM for OBS and your desktop.')
    print('Next: docs/user-guide.md. Captured Mogi results count only after you review and confirm them.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
