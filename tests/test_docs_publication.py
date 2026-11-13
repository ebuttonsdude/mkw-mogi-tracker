"""Non-spawning checks for the public documentation entry points."""

from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://github.com/ebuttonsdude/mkw-mogi-tracker'
PAGES = 'https://ebuttonsdude.github.io/mkw-mogi-tracker/'


class DocsPublicationTests(unittest.TestCase):
    def test_readme_guide_links_target_pages_and_existing_sources(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertNotRegex(readme, r'\]\(docs/[^)]+\.md')
        links = re.findall(r'\]\((https://ebuttonsdude\.github\.io/mkw-mogi-tracker/[^)]*)\)', readme)
        self.assertGreater(len(links), 10)
        for link in links:
            path = unquote(urlsplit(link).path.removeprefix('/mkw-mogi-tracker/')).rstrip('/')
            with self.subTest(link=link):
                self.assertTrue(any(candidate.is_file() for candidate in (
                    ROOT / 'docs' / (path + '.md'), ROOT / 'docs' / path / '_index.md',
                )))

    def test_ai_assistance_note_is_short_and_last(self):
        readme = (ROOT / 'README.md').read_text().strip()
        note = readme.splitlines()[-1]
        self.assertTrue(note.startswith('<sub>Made with AI assistance from '))
        self.assertTrue(note.endswith('</sub>'))
        self.assertLess(len(note), 180)
        for name in ('Qwen3.8-27b', 'Qwen3.8-Flash-Next', 'GPT-6.1-Sol', 'GPT-6-Astra'):
            self.assertIn(name, note)

    def test_public_repository_links_are_github_not_old_host(self):
        paths = [ROOT / name for name in ('README.md', 'COPYING.md', 'DESIGN.md',
                                         'website/hugo.toml', 'mkw_tracker/static/legal.html')]
        paths.extend((ROOT / 'docs').rglob('*.md'))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text()
                self.assertNotIn('gitlab', text.lower())
                self.assertNotIn('pages.sandal.tech', text)
                self.assertNotIn(REPOSITORY + '/-/', text)
        windows = (ROOT / 'docs/install/windows.md').read_text()
        self.assertIn(REPOSITORY + '/archive/refs/heads/main.zip', windows)
        self.assertIn('git clone --branch main', windows)
        self.assertIn(REPOSITORY + '/blob/main/LICENSE', (ROOT / 'mkw_tracker/static/legal.html').read_text())

    def test_update_instructions_use_github_clone_remote_without_retargeting_existing_installs(self):
        for name in ('getting-started.md', 'install/windows.md'):
            text = (ROOT / 'docs' / name).read_text()
            with self.subTest(page=name):
                self.assertIn('git pull --ff-only\n', text)
                self.assertIn('GitHub repository', text)
                self.assertNotIn('git remote set-url', text)
                self.assertNotIn('git reset --hard', text)
                self.assertIn(REPOSITORY + '/archive/refs/heads/main.zip', text)

    def test_github_workflow_runs_only_documentation_tests_and_pins_actions(self):
        workflow = (ROOT / '.github/workflows/docs.yml').read_text()
        actions = re.findall(r'uses: ([^\s]+)', workflow)
        self.assertGreaterEqual(len(actions), 5)
        for action in actions:
            self.assertRegex(action, r'^actions/[a-z-]+@[a-f0-9]{40}$')
        self.assertIn('branches: [main]', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('tests.test_docs_site tests.test_wiki_export tests.test_wiki_sync tests.test_docs_publication', workflow)
        self.assertNotIn('unittest discover', workflow)
        self.assertNotIn('tools/run_ci.sh', workflow)
        self.assertNotIn('secrets.', workflow)
        self.assertNotIn('--force', workflow)
        self.assertIn('path: .local/site-public', workflow)
        self.assertIn('python tools/sync_wiki.py --source .local/wiki-public', workflow)
        self.assertEqual(workflow.count('needs: build'), 2)


if __name__ == '__main__':
    unittest.main()
