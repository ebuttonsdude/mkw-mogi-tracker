"""Non-spawning checks for documentation-to-wiki conversion and safe output."""

from pathlib import Path, PurePosixPath
import re
import tempfile
import unittest

from tools import build_wiki


class WikiExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write('docs/_index.md', '# Welcome\n\n[Guide](guide.md#start)\n', 'Home')
        self.write('docs/guide.md', '# Guide\n\n## Start\n', 'Guide')

    def write(self, path, body, title='Example'):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'---\ntitle: "{title}"\nweight: 1\n---\n\n{body}')
        return target

    def render(self):
        return build_wiki.render_wiki(self.root)

    def test_front_matter_home_nested_slugs_and_sidebar(self):
        self.write('docs/technical/_index.md', '# Technical\n')
        self.write('docs/technical/guide.md', '# Detailed guide\n', 'Detailed guide')
        output = self.render()
        self.assertEqual(set(output), {'Home.md', 'guide.md', 'technical.md', 'technical-guide.md', '_Sidebar.md'})
        self.assertNotIn('weight:', output['Home.md'])
        self.assertIn('/wiki/guide#start)', output['Home.md'])
        self.assertIn('/wiki/technical-guide)', output['_Sidebar.md'])
        self.assertIn('/blob/main/docs/_index.md)', output['Home.md'])
        self.assertIn(build_wiki.SITE, output['Home.md'])
        self.assertIn('direct wiki edits block the next sync', output['Home.md'])

    def test_relative_links_assets_sources_and_anchors(self):
        image = self.root / 'docs/images/standings.png'
        image.parent.mkdir()
        image.write_bytes(b'approved screenshot')
        (self.root / 'LICENSE').write_text('license')
        (self.root / 'tools').mkdir()
        self.write('docs/technical/install.md', '''# Install
[Home](../_index.md)
[Guide](../guide.md#start)
[Same page](#install)
[License](../../LICENSE)
[Wrapped license
label](../../LICENSE)
[`Tools`](../../tools/)
![Screenshot](../images/standings.png "Example")
[External](https://example.test/page#anchor)
''')
        output = self.render()['technical-install.md']
        self.assertIn('/wiki/Home)', output)
        self.assertIn('/wiki/guide#start)', output)
        self.assertIn('[Same page](#install)', output)
        self.assertIn('/blob/main/LICENSE)', output)
        self.assertIn('[Wrapped license\nlabel](' + build_wiki.REPOSITORY + '/blob/main/LICENSE)', output)
        self.assertIn('[`Tools`](' + build_wiki.REPOSITORY + '/tree/main/tools)', output)
        self.assertIn(build_wiki.RAW + 'docs/images/standings.png "Example")', output)
        self.assertIn('[External](https://example.test/page#anchor)', output)

    def test_fenced_and_inline_examples_are_not_rewritten(self):
        example = '''# Examples
`[literal](missing.md)` and [real](guide.md)
```markdown
[example](missing.md)
{{< unsupported >}}
```
~~~text
[other](missing.md)
~~~
'''
        self.write('docs/example.md', example)
        output = self.render()['example.md']
        self.assertIn('`[literal](missing.md)`', output)
        self.assertIn('[real](' + build_wiki.REPOSITORY + '/wiki/guide)', output)
        self.assertIn('```markdown\n[example](missing.md)\n{{< unsupported >}}\n```', output)
        self.assertIn('~~~text\n[other](missing.md)\n~~~', output)

    def test_distro_shortcodes_become_readable_markdown(self):
        self.write('docs/linux.md', '''# Linux
{{< distro-picker >}}
{{< distro "arch" >}}
## Arch Linux
Arch steps.
{{< /distro >}}
{{< distro "fedora" >}}
## Fedora
Fedora steps.
{{< /distro >}}
{{< /distro-picker >}}
{{< distro-note "fedora" >}}
Fedora tip.
{{< /distro-note >}}
''')
        output = self.render()['linux.md']
        self.assertNotIn('{{<', output)
        for expected in ('## Arch Linux', 'Arch steps.', '## Fedora', 'Fedora steps.', '**Fedora note:**', 'Fedora tip.'):
            self.assertIn(expected, output)

    def test_unsupported_or_unbalanced_syntax_fails_closed(self):
        for body in ('{{< unknown >}}', '{{% unknown %}}', '{{< /distro >}}',
                     '{{< distro-picker >}}', '```sh\nunclosed', '<a href="guide.md">Guide</a>',
                     '[guide]: guide.md'):
            with self.subTest(body=body):
                self.write('docs/bad.md', body)
                with self.assertRaises(ValueError):
                    self.render()

    def test_missing_or_private_link_targets_are_rejected(self):
        (self.root / '.agent').mkdir()
        (self.root / '.agent/state.json').write_text('{}')
        for link in ('missing.md', '../../outside.md', '../.agent/state.json',
                     '%2e%2e/.agent/state.json', '/guide.md'):
            with self.subTest(link=link):
                self.write('docs/bad.md', f'[Bad]({link})')
                with self.assertRaises(ValueError):
                    self.render()

    def test_only_approved_images_can_be_linked(self):
        folder = self.root / 'docs/images'
        folder.mkdir()
        (folder / 'private.png').write_bytes(b'not publishable')
        self.write('docs/bad.md', '![Private](images/private.png)')
        with self.assertRaisesRegex(ValueError, 'approved documentation screenshot'):
            self.render()

    def test_provenance_and_non_markdown_files_are_not_exported(self):
        images = self.root / 'docs/images'
        images.mkdir()
        (images / 'README.md').write_text('No front matter; provenance only.')
        (self.root / 'docs/secret.json').write_text('not exported')
        self.assertEqual(set(self.render()), {'Home.md', 'guide.md', '_Sidebar.md'})

    def test_symlink_sources_and_targets_are_rejected(self):
        (self.root / 'docs/link.md').symlink_to(self.root / 'docs/guide.md')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.render()
        (self.root / 'docs/link.md').unlink()
        (self.root / 'LICENSE').symlink_to(self.root / 'docs/guide.md')
        self.write('docs/bad.md', '[License](../LICENSE)')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.render()

    def test_page_slug_collisions_are_rejected(self):
        self.write('docs/a-b.md', '# First')
        self.write('docs/a/b.md', '# Second')
        with self.assertRaisesRegex(ValueError, 'Duplicate wiki page'):
            self.render()

    def test_required_home_and_front_matter(self):
        (self.root / 'docs/_index.md').unlink()
        with self.assertRaisesRegex(ValueError, 'home page'):
            self.render()
        (self.root / 'docs/_index.md').write_text('# No front matter')
        with self.assertRaisesRegex(ValueError, 'front matter'):
            self.render()

    def test_output_requires_ownership_and_replaces_only_owned_tree(self):
        destination = self.root / 'output'
        destination.mkdir()
        sentinel = destination / 'important.txt'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'unowned'):
            build_wiki.publish_output(self.render(), destination)
        self.assertEqual(sentinel.read_text(), 'keep')
        sentinel.unlink()
        build_wiki.publish_output(self.render(), destination)
        self.assertTrue((destination / 'Home.md').is_file())
        (destination / 'obsolete.md').write_text('old generated page')
        build_wiki.publish_output(self.render(), destination)
        self.assertFalse((destination / 'obsolete.md').exists())
        self.assertEqual((destination / build_wiki.OUTPUT_MARKER).read_text(), build_wiki.MARKER_CONTENT)

    def test_output_symlink_and_forged_marker_are_rejected(self):
        destination = self.root / 'output'
        destination.symlink_to(self.root / 'docs', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build_wiki.publish_output(self.render(), destination)
        destination.unlink()
        destination.mkdir()
        marker = self.root / 'marker'
        marker.write_text(build_wiki.MARKER_CONTENT)
        (destination / build_wiki.OUTPUT_MARKER).symlink_to(marker)
        with self.assertRaisesRegex(ValueError, 'unowned'):
            build_wiki.publish_output(self.render(), destination)

    def test_output_rejects_path_escape_and_symlink_parent(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe generated'):
            build_wiki.publish_output({'../outside.md': 'No'}, self.root / 'output')
        real = self.root / 'real'
        real.mkdir()
        link = self.root / 'link'
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build_wiki.publish_output(self.render(), link / 'output')
        self.assertFalse((real / 'output').exists())

    def test_real_repository_exports_all_guides_and_resolved_wiki_links(self):
        output = build_wiki.render_wiki(build_wiki.ROOT)
        docs = [p for p in (build_wiki.ROOT / 'docs').rglob('*.md')
                if p.relative_to(build_wiki.ROOT / 'docs').parts[0] != 'images']
        self.assertEqual(len(output), len(docs) + 1)
        for name, text in output.items():
            with self.subTest(page=name):
                self.assertNotIn('{{<', text)
                for slug in re.findall(re.escape(build_wiki.REPOSITORY) + r'/wiki/([^#?\s)]+)', text):
                    self.assertIn(slug + '.md', output)
                self.assertNotRegex(text, r'\]\((?:\.\./|images/|[a-z-]+\.md)')
        self.assertEqual(build_wiki.page_slug(PurePosixPath('_index.md')), 'Home')


if __name__ == '__main__':
    unittest.main()
