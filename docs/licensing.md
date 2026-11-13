---
title: 'License & notices'
weight: 95
---

# License & notices

Copyright (C) 2026 avidgamer0 (ebuttonsdude).

MKW-Mogi-Tracker's original code is licensed under **GNU AGPLv3, version 3 only,
with a limited NVIDIA reader linking permission**. You may run, modify,
redistribute and sell it under those terms. Hosting, installation and support
can be paid services. There is **no warranty**, including no implied warranty
of merchantability or fitness for a particular purpose.

- [Public source repository](https://github.com/ebuttonsdude/mkw-mogi-tracker)
- [Full AGPLv3 license](../LICENSE)
- [Project grant and scope](../COPYING.md)
- [NVIDIA reader additional permission](../LICENSES/NVIDIA-Reader-Exception.txt)
- [Third-party inventory and distribution checklist](../THIRD_PARTY_NOTICES.md)

## Sharing modifications

If you distribute a covered modified version, follow the license's notice and
corresponding-source requirements. If people interact remotely with a modified
network service, prominently offer those users the corresponding source of that
version, free of charge, as required by section 13. An upstream link without
your modifications is not enough. You may still charge for running the service.

Local use of a modified tracker to produce an OBS broadcast does not itself
require publishing changes to viewers. Watching ordinary video output is not
the same as remotely using the tracker. Private staff interacting with the
modified tracker over a network are still remote users under section 13.

Fork maintainers must update the dashboard's `mkw_tracker/static/legal.html`
source links and the website's Source navigation to their own corresponding
source, and keep the relevant version available. The repository contains source
and build/install scripts; release distributors should identify the exact
commit or tag used. There is no local source-download endpoint.

## Third-party website notices

This website preserves full licenses for its incorporated assets:

- [Hextra — MIT](https://github.com/imfing/hextra/blob/v0.13.0/LICENSE)
- [FlexSearch — Apache-2.0](https://github.com/nextapps-de/flexsearch/blob/master/LICENSE)
- [Tailwind CSS — MIT](../LICENSES/TailwindCSS-MIT.txt)
- [Heroicons — MIT](../LICENSES/Heroicons-MIT.txt)

These notices do not relicense the tracker. The tracker also supports optional
separately installed libraries and models; they retain their own terms. In
particular, NVIDIA CUDA/CUTLASS components are not bundled, and the limited
linking permission does not grant rights to NVIDIA software. Self-contained
installers need an audit of their actual bundled libraries, notices and source
obligations, including LGPL FFmpeg where present.

Game-derived rank masks retain the separate rights/provenance boundary described
in [source and asset attribution](https://github.com/ebuttonsdude/mkw-mogi-tracker/blob/main/ATTRIBUTION.md).
This project does not grant third-party artwork or trademark rights.
