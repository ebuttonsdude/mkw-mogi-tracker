"""Non-spawning checks for the website publication and URL boundaries."""

import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import tomllib
import unittest
import urllib.error
from unittest.mock import patch

from tools import build_docs


class DocsSiteTests(unittest.TestCase):
    def test_public_guides_do_not_name_the_ci_machine(self):
        docs = Path(__file__).resolve().parents[1] / 'docs'
        for path in docs.rglob('*.md'):
            with self.subTest(path=path.relative_to(docs)):
                self.assertNotIn('watson', path.read_text(encoding='utf-8').lower())

    def test_license_navigation_and_upstream_asset_comments_are_preserved(self):
        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / 'website/hugo.toml').read_text())
        self.assertTrue(config['minify']['disableCSS'])
        self.assertTrue(config['minify']['disableJS'])
        self.assertIn({'name': 'License & notices', 'pageRef': '/licensing', 'weight': 5},
                      config['menu']['main'])

    def test_migrator_navigation_and_terminal_conflict_workflow(self):
        root = Path(__file__).resolve().parents[1]
        pages = [(root / 'docs' / name).read_text()
                 for name in ('faq.md', 'migrator.md', 'technical/_index.md')]
        weights = [int(re.search(r'^weight: (\d+)$', page, re.MULTILINE)[1]) for page in pages]
        self.assertLess(weights[0], weights[1])
        self.assertLess(weights[1], weights[2])
        guide = pages[1]
        for required in ('python -m mkw_tracker.migrate', '--apply', 'Rename', 'Skip',
                         'Overwrite', '--include-videos', '--include-models', '--everything',
                         '--preview', '--only', '--non-interactive', 'counts', 'size', 'one run'):
            self.assertIn(required, guide)
        self.assertIn('(https://ebuttonsdude.github.io/mkw-mogi-tracker/migrator/)', (root / 'README.md').read_text())
        self.assertIn('(migrator.md)', pages[0])

    def test_readme_starts_with_published_installation_and_user_guide_link(self):
        readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text()
        self.assertTrue(readme.startswith(
            '# MKW-Mogi-Tracker\n\n**[Installation instructions and user guide]'
            '(https://ebuttonsdude.github.io/mkw-mogi-tracker/)**\n'))

    def test_recognition_docs_omit_internal_validation_narratives(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('recognition.md', 'technical/recognition.md'):
            with self.subTest(page=name):
                guide = (root / 'docs' / name).read_text()
                for removed in ('Validation snapshot', 'Full-offload memory measurement',
                                'full-offload-memory-measurement', 'validation-snapshot',
                                'BlueFalcon7', 'RX 6800 XT', 'Arc B580',
                                'replaces the previously unmeasured'):
                    self.assertNotIn(removed, guide)
                self.assertNotRegex(guide, r'\b20\d{2}-\d{2}-\d{2}\b')

    def test_gguf_memory_measurement_is_present_in_both_reader_tables(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('recognition.md', 'technical/recognition.md'):
            with self.subTest(page=name):
                guide = (root / 'docs' / name).read_text()
                row = next(line for line in guide.splitlines()
                           if line.startswith('| Qwen3-VL-4B GGUF Q4_K_M |'))
                self.assertIn('**4.17 GiB** with Vulkan full offload; CPU: none', row)
                self.assertIn('Radeon RX 7900 XTX', guide)
                self.assertIn('not a minimum', guide)
                self.assertNotIn('full GPU offload: not measured', guide)
                self.assertNotIn('Vulkan full offload: unmeasured', guide)

    def test_windows_quick_start_quotes_url_and_keeps_backend_advice_simple(self):
        guide = (Path(__file__).resolve().parents[1] / 'docs/install/windows.md').read_text()
        self.assertIn('```powershell\ncd "$HOME"\ngit clone --branch main "https://github.com/ebuttonsdude/mkw-mogi-tracker.git"\n```', guide)
        paragraph = guide.split('**Recommended GGUF setup:**', 1)[1].split('\n\n', 1)[0]
        self.assertEqual(paragraph, ' choose **Vulkan GPU** if your GPU is Vulkan compatible, '
                         'or **CPU** if you do not want GPU acceleration (may be very slow).')
        for detail in ('RX 6800 XT', 'Arc B580', 'NF4 parity', 'speed advantage'):
            self.assertNotIn(detail, guide)

    def test_linux_install_has_one_shared_setup_and_accessible_picker(self):
        root = Path(__file__).resolve().parents[1]
        guide = (root / 'docs/install/linux.md').read_text()
        self.assertEqual(guide.count('git clone '), 1)
        self.assertGreater(guide.index('git clone '), guide.index('{{< /distro-picker >}}'))
        picker = (root / 'website/layouts/shortcodes/distro-picker.html').read_text()
        self.assertIn('role="tablist"', picker)
        for distro in ('arch', 'fedora', 'ubuntu'):
            self.assertIn(f'aria-controls="panel-{distro}"', picker)
        self.assertIn('{{< distro-note "fedora" >}}', guide)

    def test_base_url_requires_complete_unambiguous_http_url(self):
        self.assertEqual(build_docs.normalized_base_url('https://example.test/tracker'), 'https://example.test/tracker/')
        for value in ('/tracker/', 'file:///tmp/site', 'https://user:password@example.test/',
                      'https://example.test/?secret=value', 'https://example.test/#part',
                      'https://example.test/%2e%2e/private/'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_docs.normalized_base_url(value)

    def test_cached_download_is_checked_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            data = b'test archive'
            checksum = hashlib.sha256(data).hexdigest()
            target = cache / f'example-{checksum[:12]}.tar.gz'
            target.write_bytes(data)
            with patch.dict(build_docs.DOWNLOADS, {'example': ('https://example.invalid/archive', checksum)}), \
                    patch.object(build_docs.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
                self.assertEqual(build_docs.fetch_archive('example', cache), target)
                target.write_bytes(b'corrupted')
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    build_docs.fetch_archive('example', cache)

    def test_download_retries_transient_http_and_caches_only_verified_bytes(self):
        data = b'verified archive'
        checksum = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with patch.dict(build_docs.DOWNLOADS, {'example': ('https://example.invalid/archive', checksum)}), \
                    patch.object(build_docs.urllib.request, 'urlopen', side_effect=[
                        urllib.error.HTTPError('https://example.invalid/archive', 504, 'Gateway Time-out', {}, None),
                        io.BytesIO(data),
                    ]) as opened, patch('time.sleep') as sleep:
                target = build_docs.fetch_archive('example', cache)
                self.assertEqual(target.read_bytes(), data)
                self.assertEqual(opened.call_count, 2)
                sleep.assert_called_once_with(1)
                self.assertEqual(list(cache.iterdir()), [target])

    def test_download_failure_identifies_tool_and_bounds_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            for failure, attempts in ((504, 3), (404, 1)):
                with self.subTest(status=failure), \
                        patch.dict(build_docs.DOWNLOADS, {'example': ('https://example.invalid/archive', '0' * 64)}), \
                        patch.object(build_docs.urllib.request, 'urlopen', side_effect=lambda *a, **k:
                            (_ for _ in ()).throw(urllib.error.HTTPError(
                                'https://example.invalid/archive', failure, 'failure', {}, None))) as opened, \
                        patch('time.sleep'):
                    with self.assertRaisesRegex(OSError, 'example download failed from example.invalid'):
                        build_docs.fetch_archive('example', cache)
                    self.assertEqual(opened.call_count, attempts)
                    self.assertEqual(list(cache.iterdir()), [])

    def test_download_checksum_failure_is_not_retried_or_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with patch.dict(build_docs.DOWNLOADS, {'example': ('https://example.invalid/archive', '0' * 64)}), \
                    patch.object(build_docs.urllib.request, 'urlopen', return_value=io.BytesIO(b'wrong')) as opened:
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    build_docs.fetch_archive('example', cache)
                opened.assert_called_once()
                self.assertEqual(list(cache.iterdir()), [])

    def test_archive_member_must_be_regular_file(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as archive:
            entry = tarfile.TarInfo('hugo')
            entry.type = tarfile.SYMTYPE
            entry.linkname = '/bin/sh'
            archive.addfile(entry)
        data.seek(0)
        with tarfile.open(fileobj=data) as archive, self.assertRaises(ValueError):
            build_docs.archive_file(archive, 'hugo')

    def test_site_checks_files_anchors_and_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'guide').mkdir()
            (root / 'index.html').write_text('<a href="/tracker/guide/#start">Guide</a>')
            (root / 'guide/index.html').write_text('<h1 id="start">Start</h1><a href="/tracker/">Home</a>')
            self.assertEqual(build_docs.validate_site(root, 'https://example.test/tracker/'), 2)
            (root / 'index.html').write_text('<a href="/guide/">Wrong root</a>')
            with self.assertRaisesRegex(ValueError, 'escapes deployment prefix'):
                build_docs.validate_site(root, 'https://example.test/tracker/')
            (root / 'index.html').write_text('<a href="guide/#missing">Wrong anchor</a>')
            with self.assertRaisesRegex(ValueError, 'missing anchor'):
                build_docs.validate_site(root, 'https://example.test/tracker/')

    def test_site_rejects_remote_runtime_scripts_but_allows_source_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = root / 'index.html'
            page.write_text('<a href="https://gitlab.example/repository">Source</a>')
            self.assertEqual(build_docs.validate_site(root, 'https://example.test/'), 1)
            page.write_text('<script src="https://cdn.example/search.js"></script>')
            with self.assertRaisesRegex(ValueError, 'external runtime asset'):
                build_docs.validate_site(root, 'https://example.test/')

    def test_license_page_must_link_to_published_local_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'licensing').mkdir()
            (root / 'licenses').mkdir()
            page = root / 'licensing/index.html'
            names = (*build_docs.LEGAL_NOTICES.values(), 'hextra.txt', 'flexsearch.txt')
            for name in names:
                (root / 'licenses' / name).write_text('License text')
            for prefix in ('/', '/tracker/'):
                with self.subTest(prefix=prefix):
                    page.write_text(''.join(f'<a href="{prefix}licenses/{name}">{name}</a>' for name in names))
                    build_docs.validate_license_notices(root, 'https://example.test' + prefix)
                    page.write_text(page.read_text().replace(prefix + 'licenses/',
                                                            'https://gitlab.example/blob/main/licenses/'))
                    with self.assertRaisesRegex(ValueError, 'Missing local license'):
                        build_docs.validate_license_notices(root, 'https://example.test' + prefix)
            page.write_text(''.join(f'<a href="/licenses/{name}">{name}</a>' for name in names))
            (root / 'licenses' / names[0]).unlink()
            with self.assertRaisesRegex(ValueError, 'Missing local license'):
                build_docs.validate_license_notices(root, 'https://example.test/')

    def test_output_never_replaces_unowned_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            (source / 'index.html').write_text('new docs')
            destination = root / 'output'
            destination.mkdir()
            (destination / 'scores.sqlite').write_text('leave alone')
            with self.assertRaisesRegex(ValueError, 'unowned'):
                build_docs.publish_output(source, destination)
            self.assertEqual((destination / 'scores.sqlite').read_text(), 'leave alone')
            destination = root / 'generated'
            build_docs.publish_output(source, destination)
            (source / 'index.html').write_text('updated docs')
            build_docs.publish_output(source, destination)
            self.assertEqual((destination / 'index.html').read_text(), 'updated docs')

    @unittest.skipIf(sys.platform == "win32", "Creating symlinks requires Windows developer mode or elevation.")
    def test_stage_rejects_symlinks_in_publication_trees(self):
        for relative in ('website', 'website/static', 'website/static/nested',
                         'website/static/private.txt', 'docs', 'docs/images',
                         'LICENSE', 'LICENSES', 'LICENSES/TailwindCSS-MIT.txt'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'repository'
                stage = Path(temporary) / 'stage'
                stage.mkdir()
                for name in ('layouts', 'assets', 'static/nested'):
                    (root / 'website' / name).mkdir(parents=True)
                (root / 'website/hugo.toml').write_text('title = "Example"')
                (root / 'docs/images').mkdir(parents=True)
                (root / 'docs/_index.md').write_text('# Start here')
                for name in build_docs.SCREENSHOTS:
                    (root / 'docs/images' / name).write_bytes(b'\x89PNG\r\n\x1a\nexample')
                for name in build_docs.LEGAL_NOTICES:
                    legal = root / name
                    legal.parent.mkdir(parents=True, exist_ok=True)
                    legal.write_text('License notice')
                link = root / relative
                outside = Path(temporary) / 'private'
                if link.exists():
                    link.rename(outside)
                    link.symlink_to(outside, target_is_directory=True)
                else:
                    outside.write_text('not for publication')
                    link.symlink_to(outside)
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    build_docs.stage_sources(root, stage)

    def test_stage_only_publishes_docs_and_approved_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'repository'
            stage = Path(temporary) / 'stage'
            stage.mkdir()
            for name in ('layouts', 'assets', 'static'):
                (root / 'website' / name).mkdir(parents=True)
            (root / 'website/hugo.toml').write_text('title = "Example"')
            (root / 'docs/images').mkdir(parents=True)
            (root / 'docs/_index.md').write_text('# Start here')
            (root / 'docs/credentials.json').write_text('private')
            (root / 'docs/images/private.png').write_bytes(b'private')
            for name in build_docs.SCREENSHOTS:
                (root / 'docs/images' / name).write_bytes(b'\x89PNG\r\n\x1a\nexample')
            for name in build_docs.LEGAL_NOTICES:
                legal = root / name
                legal.parent.mkdir(parents=True, exist_ok=True)
                legal.write_text(f'License notice for {name}\n')
            (root / 'LICENSES/private.txt').write_text('not for publication')
            (root / '.env').write_text('private credentials')
            build_docs.stage_sources(root, stage)
            themes = json.loads((stage / 'data/characters.json').read_text())
            self.assertEqual(len(themes), 50)
            self.assertEqual(themes[0]['tokens'], build_docs.all_themes()[0]['tokens'])
            self.assertEqual(set(themes[0]), {'id', 'name', 'tokens'})
            self.assertTrue((stage / 'content/_index.md').is_file())
            self.assertFalse((stage / 'content/credentials.json').exists())
            self.assertFalse((stage / 'static/images/private.png').exists())
            self.assertTrue((stage / 'static/images/standings.png').is_file())
            for relative, name in build_docs.LEGAL_NOTICES.items():
                self.assertEqual((stage / 'static/licenses' / name).read_bytes(),
                                 (root / relative).read_bytes())
            self.assertEqual({path.name for path in (stage / 'static/licenses').iterdir()},
                             set(build_docs.LEGAL_NOTICES.values()))
            self.assertFalse((stage / 'static/.env').exists())
            legal_links = json.loads((stage / 'data/license_links.json').read_text())
            self.assertEqual(legal_links, {
                **{f'../{relative}': f'licenses/{name}'
                   for relative, name in build_docs.LEGAL_NOTICES.items()},
                **build_docs.UPSTREAM_LEGAL_LINKS,
            })
            (root / 'LICENSE').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing publication license'):
                build_docs.stage_sources(root, stage)


if __name__ == '__main__':
    unittest.main()
