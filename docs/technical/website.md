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

Cached archives are checksum-checked on every use. Transient download failures receive up to three attempts; errors identify the tool and download host. In CI, these archives are prepared once in the [reusable toolchain](contributing.md#reusable-ci-tools), so warm browser and Pages jobs do not download them again.

Output goes to `.local/site-public/`. The site serves its search runtime locally rather than requiring a third-party CDN. **License & notices** links to the project's license and the theme, search, CSS and icon notices. The build retains upstream CSS/JavaScript license comments and copies only the explicitly listed legal files; third-party components keep their own licenses.

## Publish with GitLab Pages

The default-branch Pages job accepts **public or private** project visibility.
Missing or unsupported visibility values fail before the build starts. Changing
the repository from private to public does not require a different build command.

Repository visibility and **Pages access control are separate settings**. The
GitLab instance enforces the selected Pages audience; the CI script does not
change or verify that setting. To make a public project's documentation public,
its Pages access setting must also permit that audience. A private deployment
can retain its project-member-only Pages access.

Publication still uses the verified toolchain
image in a disposable private-PID container. It streams tracked source into the
container and returns only the generated documentation; it never mounts the
checkout, credentials, devices or Docker socket inside the build container.

## Preview

```sh
python3 -m http.server 1313 --bind 127.0.0.1 --directory .local/site-public
```

Open **http://127.0.0.1:1313**. Stop only this preview server with Ctrl+C in its terminal when done. Do not use the tracker's usual port or data directory for the website.

## Official references

- [Hextra setup](https://imfing.github.io/hextra/docs/getting-started/)
- [Hextra deployment](https://imfing.github.io/hextra/docs/guide/deploy-site/)
