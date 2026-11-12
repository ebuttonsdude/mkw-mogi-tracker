"""Setup planning uses mocked subprocesses and never changes installed environments."""
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.name_engines import QWEN_PROFILES
from mkw_tracker import reader_setup

spec = importlib.util.spec_from_file_location('setup_neural', Path(__file__).parents[1] / 'tools/setup_neural.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class NeuralSetupTests(unittest.TestCase):
    def test_gguf_route_bypasses_cuda_and_python_dependencies(self):
        from tools import setup_gguf
        for backend in ('cpu', 'vulkan'):
            with self.subTest(backend=backend), \
                    patch.object(setup_gguf, 'main', return_value=0) as install, \
                    patch.object(setup.subprocess, 'run', side_effect=AssertionError('Unexpected process')), \
                    patch.object(setup.shutil, 'which', side_effect=AssertionError('Unexpected dependency probe')), \
                    patch('sys.argv', ['setup_neural.py', '--engine', 'qwen-4b-gguf', '--gguf-backend', backend]):
                setup.main()
                install.assert_called_once_with(['--backend', backend])

    def test_gguf_failure_propagates_and_dry_run_is_forwarded(self):
        from tools import setup_gguf
        with patch.object(setup_gguf, 'main', return_value=1) as install, \
                patch('sys.argv', ['setup_neural.py', '--engine', 'qwen-4b-gguf', '--dry-run']):
            with self.assertRaises(SystemExit) as error:
                setup.main()
            self.assertEqual(error.exception.code, 1)
            install.assert_called_once_with(['--backend', 'cpu', '--dry-run'])

    def test_quantized_installs_are_optional_and_use_pinned_tested_stacks(self):
        for engine, profile in QWEN_PROFILES.items():
            commands = setup.dependency_commands(engine, '/isolated/bin/python', '/usr/bin/uv')
            for command in commands:
                self.assertEqual(command[:5], ['/usr/bin/uv', 'pip', 'install', '--python', '/isolated/bin/python'])
            joined = ' '.join(' '.join(command) for command in commands)
            if profile['requires_nvfp4']:
                for pin in ('torch==2.14.1', 'cu130', 'transformers==5.20.2',
                            'flashinfer-python==0.7.2.post1', 'nvidia-cutlass-dsl[cu13]==4.8.1'):
                    self.assertIn(pin, joined)
            else:
                self.assertIn('torch==2.10.0', joined)
                self.assertIn('transformers==4.57.6', joined)
            self.assertEqual('bitsandbytes==0.50.2' in joined, profile['precision'] in {'int8', 'nf4'})

    def test_setup_routes_profiles_without_installing_or_loading_real_models(self):
        for engine, profile in QWEN_PROFILES.items():
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                reader_setup.write(root, {'default': 'auto', 'removed': [engine]})
                with patch.object(setup.sys, 'platform', 'linux'), \
                        patch.object(setup, 'ROOT', root), patch.object(setup.os, 'chdir'), \
                        patch.object(setup.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
                        patch.object(setup.subprocess, 'run') as run, \
                        patch('sys.argv', ['setup_neural.py', '--engine', engine]):
                    setup.main()
                self.assertNotIn(engine, reader_setup.read(root)['removed'])
                commands = [call.args[0] for call in run.call_args_list]
                python = str(root / '.local' / profile['environment'] / 'bin/python')
                self.assertTrue(all(call.kwargs.get('check') for call in run.call_args_list))
                self.assertEqual(commands[0][:4], ['/usr/bin/uv', 'venv', '--python', '3.12'])
                self.assertEqual(commands[1:1 + len(setup.dependency_commands(engine, python, '/usr/bin/uv'))],
                                 setup.dependency_commands(engine, python, '/usr/bin/uv'))
                self.assertIn([python, '-c', setup.cuda_probe(engine)], commands)
                self.assertEqual(commands[-1], ['/usr/bin/hf', 'download', profile['model_id'], '--revision',
                                               profile['revision'], '--local-dir',
                                               str(root / '.local/models' / profile['model_dir'])])
                kernel_calls = [c for c in commands if len(c) == 3 and 'kernels.get_kernel' in c[-1]]
                self.assertEqual(len(kernel_calls), int(profile['requires_nvfp4']))
                if kernel_calls:
                    pins = setup.NVFP4_KERNELS if profile['family'] == '35' else setup.NVFP4_KERNELS[:1]
                    for _, pin in pins:
                        self.assertIn(pin, kernel_calls[0][-1])
                    self.assertLess(commands.index([python, '-c', setup.cuda_probe(engine)]),
                                    commands.index(kernel_calls[0]))

    def test_nvfp4_rejects_unsupported_os_before_any_setup_or_download(self):
        for platform in ('win32', 'darwin'):
            for engine, profile in QWEN_PROFILES.items():
                if not profile['requires_nvfp4']:
                    continue
                for uv in (None, '/mock/bin/uv'):
                    with self.subTest(platform=platform, engine=engine, uv=uv), \
                            patch.object(setup.sys, 'platform', platform), \
                            patch.object(setup.os, 'chdir') as chdir, \
                            patch.object(setup.shutil, 'which', return_value=uv) as which, \
                            patch.object(setup.subprocess, 'run') as run, \
                            patch('sys.argv', ['setup_neural.py', '--engine', engine]):
                        with self.assertRaises(SystemExit) as error:
                            setup.main()
                        message = str(error.exception)
                        for text in (engine, 'native Windows', 'Linux-only',
                                     'flashinfer-python==0.7.2.post1', 'WSL2', '--engine', 'paddle'):
                            self.assertIn(text, message)
                        chdir.assert_not_called()
                        which.assert_not_called()
                        run.assert_not_called()

    def test_windows_non_nvfp4_engines_still_reach_model_download(self):
        for engine in setup.MODELS:
            if engine != 'paddle' and QWEN_PROFILES[engine]['requires_nvfp4']:
                continue
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(setup.sys, 'platform', 'win32'), \
                        patch.object(setup, 'ROOT', root), patch.object(setup.os, 'chdir'), \
                        patch.object(setup.shutil, 'which', side_effect=lambda name: '/mock/bin/' + name), \
                        patch.object(setup.subprocess, 'run') as run, \
                        patch('sys.argv', ['setup_neural.py', '--engine', engine]):
                    setup.main()
                commands = [call.args[0] for call in run.call_args_list]
                repo, revision, _, model_name = setup.MODELS[engine]
                self.assertEqual(commands[-1], ['/mock/bin/hf', 'download', repo, '--revision', revision,
                                               '--local-dir', str(root / '.local/models' / model_name)])
                self.assertNotIn('flashinfer', ' '.join(' '.join(command) for command in commands))

    def test_windows_uses_scripts_python_and_hf_entry_points(self):
        for engine in setup.MODELS:
            if engine != 'paddle' and QWEN_PROFILES[engine]['requires_nvfp4']:
                continue
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                environment = 'paddle-venv' if engine == 'paddle' else QWEN_PROFILES[engine]['environment']
                python = str(root / '.local' / environment / 'Scripts' / 'python.exe')
                with patch.object(setup.sys, 'platform', 'win32'), \
                        patch.object(setup, 'ROOT', root), patch.object(setup.os, 'chdir'), \
                        patch.object(setup.shutil, 'which', side_effect=lambda name: None if name == 'hf' else '/mock/bin/' + name), \
                        patch.object(setup.subprocess, 'run') as run, \
                        patch('sys.argv', ['setup_neural.py', '--engine', engine]):
                    setup.main()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(commands[0][:4], ['/mock/bin/uv', 'venv', '--python', '3.12'])
                dependencies = setup.dependency_commands(engine, python, '/mock/bin/uv')
                self.assertEqual(commands[1:1 + len(dependencies)], dependencies)
                if engine != 'paddle':
                    self.assertIn([python, '-c', setup.cuda_probe(engine)], commands)
                repo, revision, _, model_name = setup.MODELS[engine]
                self.assertEqual(commands[-1], [str(root / '.local' / environment / 'Scripts' / 'hf.exe'),
                                               'download', repo, '--revision', revision,
                                               '--local-dir', str(root / '.local/models' / model_name)])

    def test_windows_paths_with_spaces_and_unicode_survive_command_lines(self):
        base = Path(tempfile.mkdtemp(prefix='mkw-setup-neural-'))
        self.addCleanup(shutil.rmtree, base)
        root = base / 'My Project 日本語'
        root.mkdir()
        with patch.object(setup.sys, 'platform', 'win32'), \
                patch.object(setup, 'ROOT', root), patch.object(setup.os, 'chdir'), \
                patch.object(setup.shutil, 'which', side_effect=lambda name: '/mock/bin/' + name), \
                patch.object(setup.subprocess, 'run') as run, \
                patch('sys.argv', ['setup_neural.py', '--engine', 'paddle']):
            setup.main()
        commands = [call.args[0] for call in run.call_args_list]
        python = str(root / '.local' / 'paddle-venv' / 'Scripts' / 'python.exe')
        self.assertIn('My Project 日本語', python)
        self.assertEqual(commands[0][-1], str(root / '.local' / 'paddle-venv'))
        self.assertEqual(commands[1][4], python)
        self.assertEqual(commands[-1][-1], str(root / '.local/models' / 'paddle-ppocrv5'))

    def test_help_separates_nvfp4_os_gpu_cuda_and_memory_requirements(self):
        output = io.StringIO()
        with patch.object(setup.sys, 'platform', 'win32'), \
                patch.object(setup.sys, 'stdout', output), \
                patch.object(setup.subprocess, 'run') as run, \
                patch('sys.argv', ['setup_neural.py', '--help']):
            with self.assertRaises(SystemExit) as error:
                setup.main()
        self.assertEqual(error.exception.code, 0)
        help_text = output.getvalue()
        for engine, profile in QWEN_PROFILES.items():
            if profile['requires_nvfp4']:
                self.assertIn(f'{engine} ({profile["label"]})', help_text)
                self.assertIn(f'Memory: at least {profile["min_free_gib"]} GiB free GPU memory', help_text)
        for text in ('OS: Linux', 'native Windows', 'flashinfer-python==0.7.2.post1',
                     'GPU: NVIDIA Blackwell SM120/121', 'CUDA: 13 or newer'):
            self.assertIn(text, help_text)
        run.assert_not_called()

    def test_probe_rejects_rocm_and_checks_nvfp4_specific_architecture(self):
        for engine in QWEN_PROFILES:
            code = setup.cuda_probe(engine)
            compile(code, '<mock CUDA check>', 'exec')
            self.assertIn("getattr(torch.version,'hip',None)", code)
            self.assertEqual('((12,0),(12,1))' in code, QWEN_PROFILES[engine]['requires_nvfp4'])
            self.assertNotIn('mem_get_info', code)

    def test_nf4_install_is_distinct_pinned_and_does_not_require_nvfp4_kernels(self):
        profile = QWEN_PROFILES['qwen-4b-nf4']
        self.assertEqual(profile['environment'], 'vlm-nf4-venv')
        self.assertEqual(profile['model_dir'], 'qwen3-vl-4b')
        self.assertEqual(profile['model_id'], 'Qwen/Qwen3-VL-4B-Instruct')
        self.assertEqual(profile['revision'], 'ebb281ec70b05090aa6165b016eac8ec08e71b17')
        commands = setup.dependency_commands('qwen-4b-nf4', '/isolated/bin/python', '/usr/bin/uv')
        joined = ' '.join(' '.join(c) for c in commands)
        self.assertIn('cu128', joined)
        self.assertIn('bitsandbytes==0.50.2', joined)
        self.assertNotIn('flashinfer', joined)
        self.assertNotIn('cutlass', joined)
        self.assertNotIn('SM120', setup.cuda_probe('qwen-4b-nf4'))

    def test_failed_download_preserves_removal_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reader_setup.write(root, {'default': 'auto', 'removed': ['paddle']})
            with patch.object(setup, 'ROOT', root), patch.object(setup.os, 'chdir'), \
                    patch.object(setup.shutil, 'which', return_value='/mock/uv'), \
                    patch.object(setup.subprocess, 'run', side_effect=OSError('download failed')), \
                    patch('sys.argv', ['setup_neural.py', '--engine', 'paddle']):
                with self.assertRaises(OSError):
                    setup.main()
            self.assertIn('paddle', reader_setup.read(root)['removed'])
