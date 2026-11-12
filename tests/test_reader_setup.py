"""Non-spawning setup-management regression tests."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker import reader_setup as setup


class ReaderSetupTests(unittest.TestCase):
    def test_shared_assets_and_default_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for engine in ('qwen', 'qwen-nvfp4', 'qwen36-nvfp4'):
                for asset in setup.assets(engine):
                    (root / '.local' / asset).mkdir(parents=True, exist_ok=True)
            for engine in ('qwen', 'qwen-nvfp4', 'qwen36-nvfp4'):
                env, model = setup.assets(engine)
                executable = root / '.local' / env / ('Scripts/python.exe' if setup.sys.platform == 'win32' else 'bin/python')
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.touch()
                (root / '.local' / model / 'config.json').write_text('{}')
                (root / '.local' / model / 'model.safetensors').touch()
            setup.set_default(root, 'qwen-nvfp4')
            with self.assertRaises(ValueError):
                setup.remove(root, 'qwen-nvfp4')
            setup.remove(root, 'qwen-nvfp4', 'qwen')
            self.assertFalse(setup.installed(root, 'qwen-nvfp4'))
            self.assertTrue(setup.installed(root, 'qwen'))
            self.assertTrue(setup.installed(root, 'qwen36-nvfp4'))
            self.assertEqual(setup.read(root)['default'], 'qwen')

    def test_symlink_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.local/models').mkdir(parents=True)
            external = root / 'external'
            external.mkdir()
            (root / '.local/models/qwen3-vl-4b-gguf').symlink_to(external)
            with self.assertRaises(ValueError):
                setup.remove(root, 'qwen-4b-gguf')
            self.assertFalse((root / '.local/reader-setup.json').exists())
            self.assertTrue(external.is_dir())

    def test_malformed_preferences_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.local').mkdir()
            path = root / '.local/reader-setup.json'
            for content in ('broken', '{"default":[],"removed":[]}', '{"default":"auto","removed":[{}]}'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    setup.read(root)
                self.assertEqual(setup.public_preferences(root)['default'], 'tesseract')
                self.assertIsNotNone(setup.public_preferences(root)['warning'])

    def test_incomplete_reader_not_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for asset in setup.assets('paddle'):
                (root / '.local' / asset).mkdir(parents=True)
            self.assertFalse(setup.installed(root, 'paddle'))

    def test_removed_reader_rejected_before_launch_even_direct_constructor(self):
        from mkw_tracker.neural_ocr import NeuralNameReader, make_name_reader
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup.write(root, {'default': 'auto', 'removed': ['paddle']})
            with patch('pathlib.Path.cwd', return_value=root), patch('subprocess.Popen', side_effect=AssertionError('launch')):
                for constructor in (NeuralNameReader, make_name_reader):
                    with self.assertRaisesRegex(ValueError, 'removed'):
                        constructor('paddle')

    def test_partial_removal_stays_disabled_and_never_changes_external_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.local/models/qwen3-vl-4b-gguf').mkdir(parents=True)
            with patch.object(setup.shutil, 'rmtree', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    setup.remove(root, 'qwen-4b-gguf')
            self.assertIn('qwen-4b-gguf', setup.read(root)['removed'])

    def test_sole_bf16_removal_releases_weights_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for asset in setup.assets('qwen'):
                (root / '.local' / asset).mkdir(parents=True)
            setup.remove(root, 'qwen')
            for asset in setup.assets('qwen'):
                self.assertFalse((root / '.local' / asset).exists())

    def test_partial_unrelated_environment_does_not_pin_shared_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for asset in (*setup.assets('qwen'), 'vlm-int8-venv'):
                (root / '.local' / asset).mkdir(parents=True, exist_ok=True)
            setup.remove(root, 'qwen')
            self.assertFalse((root / '.local/models/qwen3-vl-8b').exists())
            self.assertTrue((root / '.local/vlm-int8-venv').exists())
