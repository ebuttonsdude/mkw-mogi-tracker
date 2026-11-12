#!/usr/bin/env python3
"""Install pinned, project-local multilingual name recognition models."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mkw_tracker.reader_setup import enable as enable_reader, read as read_reader_setup  # noqa: E402
from mkw_tracker.name_engines import GGUF_PROFILE, QWEN_PROFILES  # noqa: E402
from mkw_tracker.neural_ocr import venv_executable  # noqa: E402

MODELS = {
    'paddle': ('PaddlePaddle/PP-OCRv5_server_rec', 'b26c3587fda8da3c8ec0ce357214b4d661ff1558',
               'paddle-venv', 'paddle-ppocrv5'),
    **{key: (profile['model_id'], profile['revision'], profile['environment'], profile['model_dir'])
       for key, profile in QWEN_PROFILES.items()},
}

NVFP4_KERNELS = (
    ('kernels-community/nvfp4-gemm', 'd9b0c745a36a5506240fa059b97d0222bf66b644'),
    ('kernels-community/fla', '24d8640db48c804a224b5da987b81093ff7136d1'),
    ('kernels-community/mamba-ssm', '193e6bab0ec6efd5eb778f8e936cfdd4c6e58eb0'),
)


def dependency_commands(engine, python, uv):
    """Return optional-environment installs without modifying the tracker environment."""
    install = [uv, 'pip', 'install', '--python', python]
    if engine == 'paddle':
        return [install + ['paddleocr==3.3.3', 'paddlepaddle==3.2.2', 'huggingface-hub==0.36.2']]
    if QWEN_PROFILES[engine]['requires_nvfp4']:
        return [
            install + ['torch==2.14.1', 'torchvision==0.29.1',
                       '--index-url', 'https://download.pytorch.org/whl/cu130'],
            install + ['transformers==5.20.2', 'accelerate==1.12.0', 'huggingface-hub==2.3.0',
                       'kernels==0.17.2', 'flashinfer-python==0.7.2.post1',
                       'nvidia-cutlass-dsl[cu13]==4.8.1', 'einops==0.8.2', 'Pillow==13.0.0'],
        ]
    packages = ['transformers==4.57.6', 'accelerate==1.12.0', 'huggingface-hub==0.36.2', 'Pillow']
    if QWEN_PROFILES[engine]['precision'] in {'int8', 'nf4'}:
        packages.append('bitsandbytes==0.50.2')
    return [install + ['torch==2.10.0', 'torchvision==0.25.0',
                       '--index-url', 'https://download.pytorch.org/whl/cu128'], install + packages]


def cuda_probe(engine):
    """Check architecture/runtime before downloading large weights, not current free memory."""
    code = ("import torch; assert not getattr(torch.version,'hip',None) and torch.version.cuda, "
            "'This Qwen runtime requires NVIDIA CUDA, not Radeon/ROCm'; "
            "assert torch.cuda.is_available(), 'CUDA unavailable'; ")
    if QWEN_PROFILES[engine]['requires_nvfp4']:
        code += ("assert torch.cuda.get_device_capability(0) in ((12,0),(12,1)), "
                 "'This NVFP4 backend requires NVIDIA Blackwell SM120/121, not RTX 4070 or Radeon'; "
                 "assert int(torch.version.cuda.split('.')[0]) >= 13, "
                 "'Use the supported CUDA 13 NVFP4 environment'; ")
    code += ("x=torch.ones((16,16),device='cuda'); assert (x@x).sum().item()>0; "
             "print('CUDA verified:',torch.cuda.get_device_name())")
    return code


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    nvfp4_help = '\n\n'.join(
        f'{engine} ({profile["label"]}):\n'
        '  OS: Linux; native Windows and macOS are unsupported because\n'
        '      flashinfer-python==0.7.2.post1 is Linux-only. WSL2 requires Linux Python\n'
        '      and NVIDIA CUDA GPU access; this route is not validated here.\n'
        '  GPU: NVIDIA Blackwell SM120/121, not RTX 4070 or Radeon.\n'
        '  CUDA: 13 or newer.\n'
        f'  Memory: at least {profile["min_free_gib"]} GiB free GPU memory at runtime.\n'
        '          Setup checks architecture/runtime, not current free memory.'
        for engine, profile in QWEN_PROFILES.items() if profile['requires_nvfp4']
    )
    parser = argparse.ArgumentParser(description=__doc__, epilog=nvfp4_help,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--engine', choices=[*MODELS, GGUF_PROFILE['engine']], default='qwen')
    parser.add_argument('--gguf-backend', choices=('cpu', 'vulkan'), default=None)
    parser.add_argument('--dry-run', action='store_true', help='Preview GGUF downloads without installing.')
    args = parser.parse_args()
    if args.engine == GGUF_PROFILE['engine']:
        from tools import setup_gguf
        arguments = ['--backend', args.gguf_backend or 'cpu']
        if args.dry_run:
            arguments.append('--dry-run')
        result = setup_gguf.main(arguments)
        if result:
            raise SystemExit(result)
        return
    if args.gguf_backend or args.dry_run:
        parser.error('--gguf-backend and --dry-run require --engine qwen-4b-gguf')
    if (args.engine != 'paddle' and QWEN_PROFILES[args.engine]['requires_nvfp4']
            and sys.platform != 'linux'):
        raise SystemExit(
            f'{args.engine} NVFP4 setup is unsupported on {sys.platform}: '
            'flashinfer-python==0.7.2.post1 is Linux-only, with no native Windows or macOS support. '
            'Run setup with Linux Python on Linux or WSL2 with NVIDIA CUDA GPU access '
            '(WSL2 is not validated here), or select a non-NVFP4 profile with '
            '--engine qwen, qwen-int8, qwen-4b-nf4, or paddle. '
            'See --help for separate OS, GPU, CUDA and memory requirements. '
            'No environment, dependencies, kernels or models have been downloaded.')
    read_reader_setup(ROOT)
    os.chdir(ROOT)
    uv = shutil.which('uv')
    if not uv:
        raise SystemExit('uv is required to keep the Python 3.12 model environment separate from the tracker.')
    repo, revision, env_name, model_name = MODELS[args.engine]
    environment = ROOT / '.local' / env_name
    if not environment.exists():
        env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(ROOT / '.local/python'))
        subprocess.run([uv, 'venv', '--python', '3.12', str(environment)], env=env, check=True)
    python = str(venv_executable(environment, 'python'))
    for command in dependency_commands(args.engine, python, uv):
        subprocess.run(command, check=True)
    if args.engine != 'paddle':
        subprocess.run([python, '-c', cuda_probe(args.engine)], check=True)
        if QWEN_PROFILES[args.engine]['requires_nvfp4']:
            # Cache exact kernel revisions during explicit setup, not an implicit capture start.
            kernels = NVFP4_KERNELS if QWEN_PROFILES[args.engine]['family'] == '35' else NVFP4_KERNELS[:1]
            code = 'import kernels; ' + '; '.join(
                f'kernels.get_kernel({repo!r}, revision={pin!r})' for repo, pin in kernels)
            subprocess.run([python, '-c', code], check=True)
    hf = shutil.which('hf') or str(venv_executable(environment, 'hf'))
    target = ROOT / '.local/models' / model_name
    subprocess.run([hf, 'download', repo, '--revision', revision, '--local-dir', str(target)], check=True)
    enable_reader(ROOT, args.engine)
    print(f'{args.engine} name recognition ready at {target}')
    print(f'Run: {venv_executable(".venv", "python")} -m mkw_tracker')
    print('Select the installed name reader in Capture & review or Videos. Results still require confirmation.')


if __name__ == '__main__':
    main()
