# Third-party attribution and asset policy

The project's original code is licensed as described in [COPYING.md](COPYING.md):
GNU AGPLv3, version 3 only, with the limited NVIDIA reader permission.
Third-party materials retain their own rights and licenses.

Removed: decorative character/course artwork (including duplicate source directories), documentation screenshots, gameplay still fixtures, and the Racers Delight font/metadata. Existing palette values are retained. Course/player text and recognition code remain. Embedded rank-number masks in mkw_tracker/profiles/rank_templates.json were extracted from gameplay and intentionally retained by the user to preserve recognition behavior. Do not describe this as a repository with zero game-derived material.

Users are responsible for images they supply. Runtime images/themes are local data excluded from source and distribution archives. Synthetic test images must be generated or have clear independent provenance.

The retained game-derived rank masks are not covered by a grant of Nintendo or
other third-party rights. Their extraction and retention are documented, not a
claim that redistribution rights have been cleared. Attribution and the owner's
decision to retain them do not resolve copyrightability or applicable exceptions.
Redistributors must assess this asset boundary separately.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency and website
notices, distribution boundaries and remaining review requirements.

## Optional GGUF reader

The optional setup downloads [Qwen's official Qwen3-VL-4B-Instruct GGUF weights](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF) and F16 vision projector (Apache-2.0), plus [llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT). Exact revisions, release archives, and SHA-256 hashes are pinned in the reader profile/setup tool. Setup retains the upstream license texts alongside those local downloads. Models, executables, and their bundled third-party libraries are runtime data, not included in this repository or its distribution archives. Their licenses do not assign a license to the tracker itself.

## External Discord presence image

At the owner's request, enabled Discord Rich Presence references a public
[square Conkdor head/map icon from Mario Kart World](https://www.mariowiki.com/File:MK_World_map_Conkdor.png),
hosted by Super Mario Wiki (`mario.wiki.gallery`). This is third-party Nintendo
artwork, not artwork licensed under this project's AGPL. The repository and
packages contain only its URL, not image bytes. Discord retrieves the remote
image; the tracker does not upload local theme images. This reference is not a
claim of permission to redistribute the artwork. Availability and Discord's
image proxy/cache behavior remain outside the tracker's control.
