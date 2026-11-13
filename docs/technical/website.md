---
title: "Build and publish the documentation website"
weight: 20
---

# Build and publish the documentation website

The website is static documentation, not a hosted tracker. Its source pages are the Markdown files under `docs/`; do not maintain a second copy of their text.

The build uses pinned **Hugo extended 0.152.2**, **Hextra 0.13.0**, and **FlexSearch 0.8.143**. These are website-only dependencies. Tracker users do not need them.

## Build locally

From the repository root:

```sh
python3 tools/build_docs.py --base-url http://127.0.0.1:1313/
```

On Linux amd64, the helper downloads and caches the pinned build tools under `.local/`. On other platforms, provide a matching Hugo extended executable with `--hugo /path/to/hugo`.

Cached archives are checksum-checked on every use. Transient download failures receive up to three attempts; errors identify the tool and download host. GitHub's disposable documentation jobs download the same pinned, verified archives without installing the tracker or its optional readers.

Output goes to `.local/site-public/`. The site serves its search runtime locally rather than requiring a third-party CDN. **License & notices** links to the project's license and the theme, search, CSS and icon notices. The build retains upstream CSS/JavaScript license comments and copies only the explicitly listed legal files; third-party components keep their own licenses.

## Publish with GitHub Pages and the wiki

The main public guide is [GitHub Pages](https://ebuttonsdude.github.io/mkw-mogi-tracker/).
The [GitHub wiki](https://github.com/ebuttonsdude/mkw-mogi-tracker/wiki) is a second
view of the same `docs/` files, not an independently maintained guide.

The [GitHub repository](https://github.com/ebuttonsdude/mkw-mogi-tracker) contains
the documentation source. Edit `docs/` rather than generated wiki pages.
In GitHub, select **Settings → Pages → Source: GitHub Actions**. Enable the wiki
under **Settings → General → Features**, then create a placeholder **Home** page
once so GitHub initializes the wiki repository.

`.github/workflows/docs.yml` runs on `main` pushes, or manually from
**Actions → Publish documentation → Run workflow**. It runs the non-spawning
documentation/export checks and builds both outputs before publishing. These
publications deliberately **run documentation checks only**, independently of
application/browser testing. A failed docs check
blocks both publishers. A wiki initialization or push failure does not block the
independent Pages deployment; fix the reported prerequisite and rerun the job.

The website build uses the Pages-provided base URL, including its repository
subpath. Only `.local/site-public/` is uploaded, never the checkout or local data.
The wiki exporter removes Hugo front matter, converts its shortcodes, rewrites
navigation and image links, and generates a sidebar:

```sh
python3 tools/build_wiki.py
```

Output goes to `.local/wiki-public/`. The sync helper updates a separate wiki
clone, removes only obsolete generated pages recorded in its manifest, and
preserves unrelated pages. **Home** is the initial placeholder exception.
Generated pages must be edited in `docs/`; local edits to them conflict with the
next sync. Publishing uses the job's short-lived `GITHUB_TOKEN`, with
`contents: write` only for the wiki job. Pages deployment uses `pages: write`
and `id-token: write`. No extra personal token is stored in source.

## Preview

```sh
python3 -m http.server 1313 --bind 127.0.0.1 --directory .local/site-public
```

Open **http://127.0.0.1:1313**. Stop only this preview server with Ctrl+C in its terminal when done. Do not use the tracker's usual port or data directory for the website.

## Official references

- [Hextra setup](https://imfing.github.io/hextra/docs/getting-started/)
- [Hextra deployment](https://imfing.github.io/hextra/docs/guide/deploy-site/)
