"""CI cache boundaries with a fake Docker CLI; no containers or signals are used."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = 'sha256:' + 'a' * 64
FAKE_DOCKER = r'''#!/usr/bin/env python3
import io, json, os, pathlib, sys, tarfile
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_DOCKER_ROOT'])
with (root / 'calls').open('a') as output:
    output.write(json.dumps(args) + '\n')
state_path = root / 'image.json'
if args[:2] == ['image', 'inspect']:
    if not state_path.exists():
        sys.exit(1)
    state = json.loads(state_path.read_text())
    if os.environ.get('FAKE_BAD_LABEL'):
        state['label'] = 'wrong'
    if args[-1] not in (state['id'], state['tag']):
        sys.exit(1)
    print(state['id'] + (' ' + state['label'] if '.Config.Labels' in args[3] else ''))
elif args[0] == 'build':
    with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as archive:
        (root / 'context.json').write_text(json.dumps(archive.getnames()))
    pathlib.Path(args[args.index('--iidfile') + 1]).write_text('sha256:' + 'a' * 64)
    state_path.write_text(json.dumps({
        'id': 'sha256:' + 'a' * 64,
        'tag': args[args.index('--tag') + 1],
        'label': args[args.index('--label') + 1].split('=', 1)[1],
    }))
elif args[0] == 'run':
    with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as archive:
        (root / 'source.json').write_text(json.dumps(archive.getnames()))
    if args[-1] == 'checks' or 'DOCS_BASE_URL=' in ' '.join(args):
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
            entry = tarfile.TarInfo('result.txt')
            entry.size = 2
            archive.addfile(entry, io.BytesIO(b'ok'))
else:
    sys.exit(2)
'''


class CIToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        docker = self.bin / 'docker'
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.environment = {
            **os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'FAKE_DOCKER_ROOT': str(self.root), 'CI_RUNNER_DESCRIPTION': 'Watson',
            'CI_TOOLCHAIN_REBUILD': '0', 'CI_PROJECT_VISIBILITY': 'private',
            'CI_PAGES_URL': 'https://example.test/project',
        }
        for name in ('ci_image.sh', 'ci.Dockerfile', 'setup_local.py', 'build_docs.py',
                     'ci_container.sh', 'run_ci.sh', 'run_docs_ci.sh'):
            target = self.repo / 'tools' / name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / 'tools' / name, target)
        for name in ('__init__', 'neural_ocr', 'diagnostics', 'name_engines', 'tesseract', 'themes'):
            target = self.repo / 'mkw_tracker' / f'{name}.py'
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / 'mkw_tracker' / f'{name}.py', target)
        shutil.copyfile(ROOT / 'pyproject.toml', self.repo / 'pyproject.toml')
        self.git('init', '-q')
        self.git('config', 'user.email', 'ci@example.invalid')
        self.git('config', 'user.name', 'CI test')
        self.commit()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.environment,
                              check=True, capture_output=True, text=True).stdout

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')

    def run_script(self, script, *args, check=True):
        return subprocess.run(['bash', 'tools/' + script, *args], cwd=self.repo,
                              env=self.environment, check=check, capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]

    def prepare(self):
        result = self.run_script('ci_image.sh', 'prepare')
        self.assertEqual(result.stdout, f'CI_TOOLCHAIN_IMAGE={IDENTITY}\n')
        self.environment['CI_TOOLCHAIN_IMAGE'] = IDENTITY
        return result

    def test_cold_build_warm_reuse_and_explicit_rebuild(self):
        self.assertIn('Building', self.prepare().stderr)
        builds = [call for call in self.calls() if call[0] == 'build']
        self.assertEqual(len(builds), 1)
        self.assertNotIn('--no-cache', builds[0])
        self.assertIn('--iidfile', builds[0])
        self.assertFalse(Path(builds[0][builds[0].index('--iidfile') + 1]).exists())
        # Do not resolve the mutable tag again after the build has returned.
        calls = self.calls()
        build_index = next(index for index, call in enumerate(calls) if call[0] == 'build')
        for call in calls[build_index + 1:]:
            if call[:2] == ['image', 'inspect']:
                self.assertEqual(call[-1], IDENTITY)
        self.assertIn('Reusing', self.prepare().stderr)
        self.environment['CI_TOOLCHAIN_REBUILD'] = '1'
        self.prepare()
        builds = [call for call in self.calls() if call[0] == 'build']
        self.assertEqual(len(builds), 2)
        self.assertIn('--pull', builds[-1])
        self.assertIn('--no-cache', builds[-1])
        self.assertFalse(any('prune' in call for call in self.calls()))

    def test_content_key_ignores_unrelated_and_uncommitted_source(self):
        self.prepare()
        first = json.loads((self.root / 'image.json').read_text())['tag']
        (self.repo / 'unrelated.txt').write_text('fresh app source')
        self.commit()
        self.prepare()
        self.assertEqual(json.loads((self.root / 'image.json').read_text())['tag'], first)
        setup = self.repo / 'tools/setup_local.py'
        setup.write_text(setup.read_text() + '\n# changed dependency setup\n')
        self.prepare()
        self.assertEqual(json.loads((self.root / 'image.json').read_text())['tag'], first)
        self.commit()
        self.prepare()
        self.assertNotEqual(json.loads((self.root / 'image.json').read_text())['tag'], first)
        context = json.loads((self.root / 'context.json').read_text())
        self.assertNotIn('unrelated.txt', context)
        self.assertNotIn('.git', context)
        self.assertIn('tools/setup_local.py', context)
        self.assertIn('mkw_tracker/themes.py', context)

    def test_resolve_rejects_missing_malformed_or_wrong_label(self):
        self.assertNotEqual(self.run_script('ci_image.sh', 'resolve', check=False).returncode, 0)
        self.prepare()
        self.environment['CI_TOOLCHAIN_IMAGE'] = 'python:latest'
        self.assertNotEqual(self.run_script('ci_image.sh', 'resolve', check=False).returncode, 0)
        self.environment['CI_TOOLCHAIN_IMAGE'] = IDENTITY
        self.environment['FAKE_BAD_LABEL'] = '1'
        self.assertNotEqual(self.run_script('ci_image.sh', 'resolve', check=False).returncode, 0)

    def test_jobs_use_exact_image_with_fresh_source_and_no_host_access(self):
        self.prepare()
        (self.repo / 'fresh-source.txt').write_text('current application')
        self.commit()
        for script, args in (('run_ci.sh', ('checks',)), ('run_ci.sh', ('browser',)),
                             ('run_docs_ci.sh', ())):
            self.run_script(script, *args)
            self.assertIn('fresh-source.txt', json.loads((self.root / 'source.json').read_text()))
        runs = [call for call in self.calls() if call[0] == 'run']
        self.assertEqual(len(runs), 3)
        self.assertEqual(sum(call[0] == 'build' for call in self.calls()), 1)
        for call in runs:
            self.assertIn(IDENTITY, call)
            self.assertIn('--rm', call)
            self.assertFalse(set(call) & {'--privileged', '--pid', '--volume', '-v', '--mount', '--device'})
            self.assertNotIn('host', call)
            self.assertTrue(any('$$ == 1' in arg for arg in call))
        self.assertEqual((self.repo / 'dist/result.txt').read_text(), 'ok')
        self.assertEqual((self.repo / 'public/result.txt').read_text(), 'ok')

    def test_missing_tracked_input_fails_before_docker(self):
        self.git('rm', 'mkw_tracker/tesseract.py')
        self.commit()
        result = self.run_script('ci_image.sh', 'prepare', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Required toolchain input missing', result.stderr)
        self.assertFalse((self.root / 'calls').exists())

    def test_pages_accepts_public_and_private_without_changing_access_settings(self):
        self.prepare()
        for visibility in ('public', 'private'):
            with self.subTest(visibility=visibility):
                self.environment['CI_PROJECT_VISIBILITY'] = visibility
                result = self.run_script('run_docs_ci.sh')
                self.assertIn(f'({visibility} project; audience controlled by Pages settings)', result.stdout)
                self.assertEqual((self.repo / 'public/result.txt').read_text(), 'ok')
        runs = [call for call in self.calls() if call[0] == 'run']
        self.assertEqual(len(runs), 2)
        for call in runs:
            self.assertIn(IDENTITY, call)
            self.assertIn('--rm', call)
            self.assertFalse(set(call) & {'--privileged', '--pid', '--volume', '-v', '--mount', '--device'})
            self.assertTrue(any('$$ == 1' in arg for arg in call))
            self.assertFalse(any('CI_JOB_TOKEN' in arg or 'CI_PROJECT_VISIBILITY' in arg for arg in call))

    def test_pages_rejects_missing_or_unsupported_visibility_before_docker(self):
        for visibility in (None, '', 'internal', 'unknown', 'PUBLIC'):
            with self.subTest(visibility=visibility):
                if visibility is None:
                    self.environment.pop('CI_PROJECT_VISIBILITY', None)
                else:
                    self.environment['CI_PROJECT_VISIBILITY'] = visibility
                result = self.run_script('run_docs_ci.sh', check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Expected public or private project visibility', result.stderr)
                self.assertFalse((self.root / 'calls').exists())

    def test_public_pages_still_requires_approved_runner(self):
        self.environment['CI_PROJECT_VISIBILITY'] = 'public'
        self.environment['CI_RUNNER_DESCRIPTION'] = 'other'
        result = self.run_script('run_docs_ci.sh', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires the watson runner', result.stderr)
        self.assertFalse((self.root / 'calls').exists())

    def test_unset_runner_fails_with_clear_error(self):
        del self.environment['CI_RUNNER_DESCRIPTION']
        result = self.run_script('ci_image.sh', 'prepare', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires the watson runner', result.stderr)
        self.assertFalse((self.root / 'calls').exists())

    def test_wrong_runner_never_uses_docker(self):
        self.environment['CI_RUNNER_DESCRIPTION'] = 'other'
        result = self.run_script('ci_image.sh', 'prepare', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'calls').exists())

    def test_image_reuses_existing_setup_and_download_verification(self):
        dockerfile = (ROOT / 'tools/ci.Dockerfile').read_text()
        self.assertIn('tools/setup_local.py', dockerfile)
        self.assertIn('fetch_archive(name', dockerfile)
        self.assertIn('playwright==1.56.0', dockerfile)
        self.assertIn('playwright install --with-deps chromium', dockerfile)
        layers = dockerfile.split('\nRUN ')[1:]
        self.assertEqual(len(layers), 3)
        self.assertIn('tools/setup_local.py', layers[0])
        self.assertIn('playwright install --with-deps chromium', layers[1])
        self.assertIn('fetch_archive(name', layers[2])
        container = (ROOT / 'tools/ci_container.sh').read_text()
        self.assertNotIn('apt-get', container)
        self.assertNotIn('pip install', container)
        self.assertIn('tools/check_package.py dist', container)
        pipeline = (ROOT / '.gitlab-ci.yml').read_text()
        self.assertIn('dotenv: ci-toolchain.env', pipeline)
        self.assertEqual(pipeline.count('- job: toolchain'), 3)


if __name__ == '__main__':
    unittest.main()
