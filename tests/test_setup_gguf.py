"""GGUF installer tests use synthetic archives and intercepted network/process APIs."""

from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from mkw_tracker import reader_setup
from unittest.mock import patch
import zipfile

from tools import setup_gguf as setup


class GGUFSetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(setup, 'ROOT', self.root))
        self.stack.enter_context(patch.object(setup.sys, 'platform', 'linux'))
        self.stack.enter_context(patch.object(setup.platform, 'machine', return_value='x86_64'))
        self.network = self.stack.enter_context(patch.object(setup.urllib.request, 'urlopen', side_effect=AssertionError('Unexpected network')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Unexpected process')))
        self.stack.enter_context(patch.object(setup.sys, 'stdout', new_callable=io.StringIO))
        self.stack.enter_context(patch.object(setup.sys, 'stderr', new_callable=io.StringIO))

    def tar(self, entries):
        archive = self.root / 'runtime.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name, data, kind in entries:
                entry = tarfile.TarInfo(name)
                entry.mode = 0o755
                if kind == 'file':
                    entry.size = len(data)
                    output.addfile(entry, io.BytesIO(data))
                else:
                    entry.type = tarfile.SYMTYPE if kind == 'link' else tarfile.LNKTYPE
                    entry.linkname = data
                    output.addfile(entry)
        return archive

    def test_download_streams_checksums_and_reuses_verified_file(self):
        data = b'GGUF fixture'
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / 'download.gguf'
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(data)
        setup.download('https://example.invalid/model', target, digest)
        self.assertEqual(target.read_bytes(), data)
        setup.download('https://example.invalid/model', target, digest)
        self.network.assert_called_once()
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_bad_download_preserves_existing_file_and_cleans_partial(self):
        target = self.root / 'model.gguf'
        target.write_bytes(b'previous')
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(b'wrong')
        with self.assertRaisesRegex(ValueError, 'Checksum'):
            setup.download('https://example.invalid/model', target, '0' * 64)
        self.assertEqual(target.read_bytes(), b'previous')
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_download_size_limit(self):
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(b'oversize')
        with patch.object(setup, 'MAX_DOWNLOAD_BYTES', 2), self.assertRaisesRegex(ValueError, 'size limit'):
            setup.download('https://example.invalid/model', self.root / 'model', '0' * 64)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_tar_internal_link_chains_are_regular_files_with_executable_mode(self):
        archive = self.tar([
            ('llama-b12110/llama-server', b'fixture', 'file'),
            ('llama-b12110/lib/libggml.so.1.2', b'library', 'file'),
            ('llama-b12110/lib/libggml.so.1', 'libggml.so.1.2', 'link'),
            ('llama-b12110/libggml.so', 'lib/libggml.so.1', 'link'),
            ('llama-b12110/LICENSE', b'license', 'file'),
            ('llama-b12110/hard', 'llama-b12110/lib/libggml.so.1.2', 'hard'),
        ])
        target = self.root / 'unpacked'
        setup.extract_runtime(archive, target)
        self.assertEqual((target / 'llama-b12110/libggml.so').read_bytes(), b'library')
        self.assertFalse((target / 'llama-b12110/libggml.so').is_symlink())
        self.assertEqual((target / 'llama-b12110/hard').read_bytes(), b'library')
        self.assertTrue((target / 'llama-b12110/llama-server').stat().st_mode & 0o111)
        self.assertTrue((target / 'llama-b12110/LICENSE').is_file())

    def test_rejects_unsafe_archive_paths(self):
        for name in ('../escape', '/escape', 'C:/escape', 'dir\\escape', 'dir/../escape'):
            with self.subTest(name=name):
                archive = self.tar([(name, b'bad', 'file')])
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    with self.assertRaises(ValueError):
                        setup.extract_runtime(archive, Path(temporary) / 'out')
        self.assertFalse((self.root / 'escape').exists())

    def test_rejects_external_cyclic_and_missing_links(self):
        for links in ([('a', '../escape', 'link')], [('a', '/escape', 'link')],
                      [('a', 'b', 'link'), ('b', 'a', 'link')], [('a', 'missing', 'link')]):
            archive = self.tar(links)
            with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                with self.assertRaises(ValueError):
                    setup.extract_runtime(archive, Path(temporary) / 'out')

    def test_zip_preserves_dlls_and_license_and_rejects_links(self):
        archive = self.root / 'runtime.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for name in ('llama-server.exe', 'ggml.dll', 'LICENSE'):
                output.writestr(name, b'fixture')
        setup.extract_runtime(archive, self.root / 'win')
        self.assertEqual({p.name for p in (self.root / 'win').iterdir()}, {'llama-server.exe', 'ggml.dll', 'LICENSE'})
        with zipfile.ZipFile(archive, 'w') as output:
            link = zipfile.ZipInfo('link')
            link.external_attr = 0o120777 << 16
            output.writestr(link, 'target')
        with self.assertRaises(ValueError):
            setup.extract_runtime(archive, self.root / 'bad-win')

    def test_unsupported_platform_and_architecture_stop_before_download(self):
        for system, machine in [('darwin', 'x86_64'), ('linux', 'aarch64'), ('win32', 'ARM64')]:
            with self.subTest(system=system, machine=machine), patch.object(setup.sys, 'platform', system), \
                    patch.object(setup.platform, 'machine', return_value=machine):
                self.assertEqual(setup.main([]), 1)
        self.network.assert_not_called()
        self.assertFalse((self.root / '.local').exists())

    def test_dry_run_does_not_write_or_download(self):
        self.assertEqual(setup.main(['--backend', 'vulkan', '--dry-run']), 0)
        self.network.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_install_publishes_config_only_after_all_downloads(self):
        reader_setup.write(self.root, {'default': 'auto', 'removed': ['qwen-4b-gguf']})
        fixture = self.tar([('llama-b12110/llama-server', b'executable', 'file')])
        model = self.root / '.local/models' / setup.GGUF_PROFILE['model_dir']
        calls = []

        def fake_download(url, target, digest):
            calls.append((url, target, digest))
            self.assertFalse((model / 'runtime.json').exists())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fixture.read_bytes() if target.name.endswith('.tar.gz') else b'model')

        with patch.object(setup, 'download', side_effect=fake_download):
            setup.install('vulkan')
        self.assertNotIn('qwen-4b-gguf', reader_setup.read(self.root)['removed'])
        config = json.loads((model / 'runtime.json').read_text())
        self.assertEqual(config['backend'], 'vulkan')
        self.assertTrue(Path(config['executable']).is_absolute())
        self.assertTrue(Path(config['executable']).is_file())
        self.assertEqual(len(calls), 5)
        self.assertTrue((model / 'LICENSE-Qwen3-VL').is_file())
        self.assertEqual(calls[3][2], setup.LLAMA_LICENSE[1])
        self.assertIn(setup.GGUF_PROFILE['revision'], calls[1][0])
        self.assertEqual(calls[2][2], setup.GGUF_PROFILE['projector_sha256'])

    def test_rerun_repairs_missing_and_corrupt_companion_libraries(self):
        fixture = self.tar([
            ('llama-b12110/llama-server', b'executable', 'file'),
            ('llama-b12110/libggml.so', b'library', 'file'),
        ])

        def fake_download(url, target, digest):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fixture.read_bytes() if target.name.endswith('.tar.gz') else b'model')

        with patch.object(setup, 'download', side_effect=fake_download):
            setup.install('cpu')
            runtime = self.root / '.local/llama.cpp/b12110-linux-cpu/llama-b12110'
            library = runtime / 'libggml.so'
            unknown = runtime / 'user-note.txt'
            unknown.write_text('preserve')
            for damage in ('missing', 'corrupt'):
                with self.subTest(damage=damage):
                    if damage == 'missing':
                        library.unlink()
                    else:
                        library.write_bytes(b'corrupted')
                    setup.install('cpu')
                    self.assertEqual(library.read_bytes(), b'library')
                    self.assertEqual(unknown.read_text(), 'preserve')
                    self.assertEqual(list(runtime.glob('*.repair')), [])

    def test_repair_failure_preserves_previous_config_and_library(self):
        fixture = self.tar([
            ('llama-b12110/llama-server', b'executable', 'file'),
            ('llama-b12110/libggml.so', b'library', 'file'),
        ])

        def fake_download(url, target, digest):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fixture.read_bytes() if target.name.endswith('.tar.gz') else b'model')

        with patch.object(setup, 'download', side_effect=fake_download):
            setup.install('cpu')
            config = self.root / '.local/models' / setup.GGUF_PROFILE['model_dir'] / 'runtime.json'
            before = config.read_bytes()
            library = self.root / '.local/llama.cpp/b12110-linux-cpu/llama-b12110/libggml.so'
            library.write_bytes(b'previous')
            with patch.object(setup.os, 'replace', side_effect=PermissionError('file is in use')):
                self.assertEqual(setup.main(['--backend', 'cpu']), 1)
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(library.read_bytes(), b'previous')
            self.assertEqual(list(library.parent.glob('*.repair')), [])

    def test_repair_rejects_existing_symlink_before_changing_any_files(self):
        staging = self.root / 'staging'
        installed = self.root / 'installed'
        staging.mkdir()
        installed.mkdir()
        (staging / 'first').write_bytes(b'new')
        (staging / 'linked').write_bytes(b'library')
        (installed / 'first').write_bytes(b'previous')
        outside = self.root / 'outside'
        outside.write_bytes(b'untouched')
        try:
            (installed / 'linked').symlink_to(outside)
        except OSError as error:
            self.skipTest(f'Symlink creation unavailable: {error}')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            setup.reconcile_runtime(staging, installed)
        self.assertEqual((installed / 'first').read_bytes(), b'previous')
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_model_failure_keeps_previous_backend_config(self):
        reader_setup.write(self.root, {'default': 'auto', 'removed': ['qwen-4b-gguf']})
        fixture = self.tar([('llama-b12110/llama-server', b'executable', 'file')])
        model = self.root / '.local/models' / setup.GGUF_PROFILE['model_dir']
        model.mkdir(parents=True)
        config = model / 'runtime.json'
        config.write_text('{"backend":"cpu","executable":"previous"}')
        original = config.read_bytes()

        def fake_download(url, target, digest):
            if target.name.endswith('.tar.gz'):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fixture.read_bytes())
            else:
                raise OSError('interrupted download')

        with patch.object(setup, 'download', side_effect=fake_download):
            self.assertEqual(setup.main(['--backend', 'vulkan']), 1)
        self.assertEqual(config.read_bytes(), original)
        self.assertIn('qwen-4b-gguf', reader_setup.read(self.root)['removed'])


if __name__ == '__main__':
    unittest.main()
