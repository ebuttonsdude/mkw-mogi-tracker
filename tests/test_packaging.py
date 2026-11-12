from io import BytesIO
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from tools.check_package import check_contents, check_member, inspect_archive


class PackageChecksTests(unittest.TestCase):
    def test_rejects_overlay_source_preferences_and_atomic_writes(self):
        for name in ('overlay-profiles.json', 'custom-data/overlay-profiles.json.tmp',
                     'nested/OVERLAY-PROFILES.JSON', 'overlay-backgrounds.json',
                     'nested/OVERLAY-BACKGROUNDS.JSON', 'overlay-backgrounds-backup.json',
                     'custom-data/.overlay-backgrounds-secret.tmp'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)

    def test_rejects_external_endpoint_credentials_and_atomic_save_files(self):
        for name in ('external-vision.json', 'nested/EXTERNAL-VISION.JSON',
                     'custom-data/.external-vision-secret.tmp'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)

    def test_rejects_local_course_picture_libraries_and_temporary_files(self):
        for name in ('course-images.json', 'nested/COURSE-IMAGES.JSON',
                     'course-images-backup.json', 'custom-data/.course-images-secret.tmp'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)

    def test_rejects_runtime_secrets_models_and_case_insensitive_videos(self):
        for name in ('pkg/.agent/state.json', 'pkg/.local/frame.png', 'pkg/.omx/evidence.json', 'pkg/uploads/frame.png',
                     'pkg/OBS-connection.json', 'pkg/.obs-connection-secret.tmp', 'pkg/.env.production',
                     'pkg/scores.sqlite-wal', 'pkg/scores.db', 'pkg/scores.db.backup',
                     'pkg/model.safetensors', 'pkg/model.ONNX', 'pkg/clip.Mp4', 'pkg/clip.MKV',
                     'pkg/logs/app.log', 'pkg/custom-data/logs/http.log.1', '../outside.py', '/absolute.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)

    def test_rejects_disguised_media_and_databases(self):
        for header in (b'\x00\x00\x00\x18ftypmp42', b'\x1aE\xdf\xa3', b'RIFF1234AVI ', b'SQLite format 3\x00'):
            with self.subTest(header=header), self.assertRaises(ValueError):
                check_member('pkg/fixture.bin', header)

    def test_allows_source_and_explicit_recognition_profile_exception(self):
        for name, header in [('mkw_tracker/profiles/rank_templates.json', b'{"templates":'),
                             ('mkw_tracker/static/app.js', b'function render'),
                             ('mkw_tracker/credentials.py', b'import json')]:
            with self.subTest(name=name):
                check_member(name, header)

    def test_rejects_bundled_decorative_images_fonts_and_uploaded_themes(self):
        for name in ('tests/fixtures/frame.webp', 'mkw_tracker/static/fonts/font.otf',
                     'docs/images/dashboard.png', 'static/portrait.JPEG', 'theme-library.json', 'custom-themes.json',
                     'nested/custom-themes-backup.json', '.themes-deadbeef.tmp'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)
        for header in (b'\x89PNG\r\n\x1a\n', b'RIFF1234WEBP', b'OTTO', b'GIF89a'):
            with self.subTest(header=header), self.assertRaisesRegex(ValueError, 'signature'):
                check_member('hidden.bin', header)

    def test_archive_inspection_reads_file_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / 'example.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('mkw_tracker/hidden.bin', b'\x00\x00\x00\x18ftypmp42')
            with self.assertRaisesRegex(ValueError, 'signature'):
                inspect_archive(wheel)

    def test_source_archive_rejects_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'example.tar.gz'
            with tarfile.open(source, 'w:gz') as archive:
                entry = tarfile.TarInfo('example/docs/link.md')
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/private/file'
                archive.addfile(entry)
            with self.assertRaisesRegex(ValueError, 'Unexpected archive entry'):
                inspect_archive(source)

    def test_required_assets_and_linked_docs_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / 'mkw_tracker/static/index.html'
            asset.parent.mkdir(parents=True)
            asset.write_text('<html>')
            (root / 'README.md').write_text('[Guide](docs/guide.md#section)')
            legal_files = {'LICENSE', 'COPYING.md', 'ATTRIBUTION.md', 'THIRD_PARTY_NOTICES.md',
                           'LICENSES/NVIDIA-Reader-Exception.txt'}
            for name in legal_files:
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'license text')
            wheel = root / 'example.whl'
            source = root / 'example.tar.gz'

            def archives(include_asset=True, include_guide=True, *, omit_license=None,
                         changed_license=None, legacy_layout=False, omit_source_license=None,
                         changed_source_license=None):
                members = {'README.md', 'DESIGN.md'}
                if include_asset:
                    members.add('mkw_tracker/static/index.html')
                if include_guide:
                    members.add('docs/guide.md')
                with zipfile.ZipFile(wheel, 'w') as archive:
                    for name in members:
                        archive.writestr(name, b'content')
                    for name in legal_files:
                        if name != omit_license:
                            relative = Path(name).name if legacy_layout else f'licenses/{name}'
                            archive.writestr(f'example.dist-info/{relative}',
                                             b'wrong text' if name == changed_license else b'license text')
                with tarfile.open(source, 'w:gz') as archive:
                    for name in members | legal_files:
                        if name == omit_source_license:
                            continue
                        content = b'license text' if name in legal_files else b'content'
                        if name == changed_source_license:
                            content = b'wrong source license'
                        entry = tarfile.TarInfo('example/' + name)
                        entry.size = len(content)
                        archive.addfile(entry, BytesIO(content))

            archives()
            check_contents(wheel, source, root)
            # Website-only screenshots stay outside image-free application archives.
            (root / 'README.md').write_text(
                '[Guide](docs/guide.md#section)\n![Demo](docs/images/standings.png)')
            check_contents(wheel, source, root)
            archives(include_asset=False)
            with self.assertRaisesRegex(ValueError, 'missing bundled assets'):
                check_contents(wheel, source, root)
            archives(include_guide=False)
            with self.assertRaisesRegex(ValueError, 'missing documentation'):
                check_contents(wheel, source, root)
            archives(legacy_layout=True)
            check_contents(wheel, source, root)
            archives(omit_license='LICENSES/NVIDIA-Reader-Exception.txt')
            with self.assertRaisesRegex(ValueError, 'wheel missing or ambiguous license file'):
                check_contents(wheel, source, root)
            archives(omit_license='ATTRIBUTION.md')
            with self.assertRaisesRegex(ValueError, 'wheel missing or ambiguous license file: ATTRIBUTION.md'):
                check_contents(wheel, source, root)
            archives(changed_license='THIRD_PARTY_NOTICES.md')
            with self.assertRaisesRegex(ValueError, 'wheel license text differs'):
                check_contents(wheel, source, root)
            archives(omit_source_license='COPYING.md')
            with self.assertRaisesRegex(ValueError, 'sdist missing license files'):
                check_contents(wheel, source, root)
            archives(changed_source_license='LICENSE')
            with self.assertRaisesRegex(ValueError, 'sdist license text differs'):
                check_contents(wheel, source, root)

    def test_project_metadata_declares_grant_and_bundled_legal_files(self):
        import tomllib

        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / 'pyproject.toml').read_text())
        self.assertEqual(metadata['project']['license'], {'file': 'COPYING.md'})
        self.assertIn('License :: OSI Approved :: GNU Affero General Public License v3',
                      metadata['project']['classifiers'])
        self.assertEqual(metadata['tool']['setuptools']['license-files'],
                         ['LICENSE', 'COPYING.md', 'ATTRIBUTION.md', 'THIRD_PARTY_NOTICES.md', 'LICENSES/*.txt'])
