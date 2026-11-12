# Design

## Source of truth

Status: Active. Updated 2026-11-08.

This document describes MKW-Mogi-Tracker's current dashboard, OBS browser overlay, PNG reports, and capture/review workflow. It also records the behavior that changes must preserve. It does not depend on a private interview or files outside the repository.

The dashboard also has an isolated Worldwides mode. Rules about twelve identities, placement points, teams and explicit score confirmation below apply to Mogi. WW records observed ratings automatically under the separate rules in [Worldwides mode](#worldwides-mode).

Evidence reviewed:

- Dashboard structure, behavior, and styles in [`index.html`](mkw_tracker/static/index.html), [`app.js`](mkw_tracker/static/app.js), and [`style.css`](mkw_tracker/static/style.css).
- Overlay behavior and layout in [`overlay.js`](mkw_tracker/static/overlay.js) and [`overlay.css`](mkw_tracker/static/overlay.css).
- Scoring and output rules in [`store.py`](mkw_tracker/store.py), [`reports.py`](mkw_tracker/reports.py), [`themes.py`](mkw_tracker/themes.py), and [`settings.py`](mkw_tracker/settings.py).
- Capture, imports, connection safety, and HTTP behavior in [`capture.py`](mkw_tracker/capture.py), [`cameras.py`](mkw_tracker/cameras.py), [`imports.py`](mkw_tracker/imports.py), [`obs.py`](mkw_tracker/obs.py), [`credentials.py`](mkw_tracker/credentials.py), and [`server.py`](mkw_tracker/server.py).
- User guide and browser acceptance checks; decorative upstream screenshots are intentionally absent.

Source and regression tests determine behavior; synthetic imagery is used for theme tests. No live-capture reliability is implied.

Native Windows uses the same loopback HTTP dashboard and SQLite scoring model, not a separate UI. Manual scoring starts without capture/model dependencies. Linux V4L2 cameras remain explicitly unavailable on Windows; OBS WebSocket screenshots are the native capture route. Windows managed uploads pin directory/file handles and reject reparse points; private OBS settings use a protected owner-only DACL. Neural subprocesses use bounded JSONL transport and Windows Job Objects to terminate descendants on cancellation or timeout. Linux retains its descriptor-relative filesystem protections and explicit camera lifecycle. Neural worker teardown retains unreaped child identity until group signalling, rejects invalid or unowned targets, and blocks reuse after incomplete cleanup. Unsupported POSIX ownership APIs are rejected before worker launch. Report glyph checks preserve variation selectors and joiners in the original text. Rejected HTTP bodies have an absolute short drain deadline. See [Windows setup](docs/technical/windows-setup.md) for runtime evidence and untested live OBS/GPU boundaries.

## Brand

A playful race-night scoreboard with character colors, checkered accents, prominent scores, and readable names. Capture status, screenshot evidence, and explicit confirmation provide trust. Never imply that a disconnected input is live or that a suspected result has been verified.

Do not bundle decorative game artwork. Uploaded icons are local runtime data; preserve source attribution in ATTRIBUTION.md. Embedded recognition rank masks are retained as the documented exception.

## Product goals

- Record placement-derived player and team scores for a 12-player room.
- Keep captured results pending until the operator verifies names and identities, unless they explicitly enable experimental YOLO mode.
- Correct results and apply penalties without counting a race twice.
- Keep each saved session's roster, races, and Lounge ID independent.
- Show consistent standings in the dashboard, OBS, and PNG exports.
- Associate editable course names and recording-relative intro markers with races.
- Keep OBS screenshot acquisition fast enough to sample brief finishing tables. UI traffic must not stall acquisition or recognition.

Non-goals are live in-race positions, automatic score confirmation outside the explicit YOLO exception, automatic cloud processing, and a publicly hosted multi-user service. Success means the operator can tell what was observed, what needs review, and which results contribute points. A configured sample interval is not a guarantee that every result screen will be captured.

## Personas and jobs

The primary operator plays in a 12-person room on Arch Linux and runs the dashboard beside OBS. Between races, they review a screenshot, fix identities, and check standings. They may share OBS Virtual Camera with Discord while the tracker uses WebSocket screenshots.

After a session, the operator imports a recording, corrects course/timing details, exports chapters, or shares a PNG report. Viewers see the overlay or exported image, not a separate authenticated read-only dashboard. Friends running the source use their own local data and capture setup.

## Information architecture

The dashboard has seven tabs. Desktop tabs form a left navigation column; only the selected panel is visible.

| Tab | Contents |
| --- | --- |
| Standings | Team/player tables, assignments, colors, room progress, roster edits, and the live text-file path |
| Capture & review | Input/source controls, capture status, session creation, pending race, and screenshot identity reviews |
| Videos | Local-path/browser imports, progress/cancellation, and uploaded-copy cleanup |
| Race history | Session folders, load/rename/delete, race creation/correction, evidence, penalties, Lounge links, and PNG actions |
| Chapters | Recording references, editable intro offsets, and chapter download |
| Overlays | Named sources, Lounge cards, source font, OBS dimensions, text outputs, visibility and live preview |
| Settings | Character themes, course pictures, shared Overlay & PNG font, backups and Experimental options |

The browser remembers the selected tab and folder expansion. Opening an editor selects Race history. History defaults to newest-created folders and supports filtering and race sorting. Each folder is a scoring session, not a container for moving races between sessions. Collapse all closes every folder in the current mode, including filtered-out folders, persists expansion state and preserves unsaved editor input.

Settings keeps themes and course pictures first; Overlays owns source, display, OBS text output and browser overlay controls. Overlay & PNG font, Session backups and Experimental use native disclosures closed by default; user-opened panels survive polling. External vision endpoint lives inside Experimental. Capture/import name-reader guidance is collapsible. NVFP4 hardware reminder dismissal is per browser and model; guidance retains the warning and setup/runtime errors remain visible.

### Scoring and session rules

- A confirmed race has exactly 12 distinct player identities in finishing order. Points are `15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1`: 82 per race and 984 for twelve races. Additional races are not capped at 984.
- Pending results contribute no points. Never use the game's cumulative score as the scoring input. Corrections replace the race's contribution.
- Race-linked deductions reduce net points without changing places or gross room allocation. Gross room totals retain confirmed placements even when a player is removed from the active roster.
- The selected session's active roster and current assignments determine team totals, graph traces, and report membership, including for unloaded sessions and zero-score active players/teams. A deleted player does not remain in current team totals; a deleted team does not reappear in reports. Historical placements remain readable, not a historical membership snapshot.
- Stable IDs distinguish players with identical names. Preserve Unicode names and explicit identity choices. Automatically select only a unique exact name match, never an approximate OCR match or a team-tag guess.
- In an untouched session, the first explicit captured-roster confirmation creates editable teams from reviewed names' first Unicode characters, including symbols, numbers and Japanese characters, ignoring case. Existing or previously deleted roster records and earlier scored races disable this bootstrap. Later reviews preserve assignments.
- Creating a session starts an empty roster/history. Loading changes the displayed session and OBS output. Other sessions remain available. Deleting the selected session creates a fresh replacement; deleting another session does not load it.
- Drafts belong to the session in which the operator began them. A session change in another tab must not silently redirect a submission. Reject a stale session-bound write and explain how to refresh.

### Experimental YOLO confirmation

Settings → Experimental provides an off-by-default, persisted `yolo_mode` preference shared by Mogi Teams and FFA. Worldwides never uses it. Enabling requires idle capture/imports, a current session/mode token and explicit **Confirm - yolo** after the warning **This mode is extremely likely to make mistakes. You will likely need to edit race history, teams, and names manually.** **Cancel** or Escape leaves it disabled. Show a persistent Mogi-only active badge. Disabling is allowed while capture/import runs, invalidates in-flight result recognition, and leaves scored history and existing pending reviews unchanged.

The opt-in reader route retains the same three-distinct-+points-row phase proof and rank thresholds, but continues past unproven ranks. It reads names only for proven slots and requires at least one proven rank. Fill each absent position or empty name with a new stable `not-detected-fixme` identity; never shift a later row into a missing slot, match placeholder text, or guess approximate identities. Only unique exact existing names may be reused, once per race. New YOLO identities remain unassigned rather than bootstrapping teams.

Commit all twelve placements, new identities and `races.auto_confirmed=1` atomically. Existing/ordinary races default to `0`; corrections never change the provenance flag or original revision. History and evidence editors retain the YOLO label. Preserve screenshots and prior placement revisions. A persisted race latch prevents held frames or restart recovery from scoring twice; a new recognized course intro is required to rearm YOLO. Session switches never redirect a latched draft: old-session partial results become manual reviews, and an in-flight live result is discarded if the active session changes. Video imports retain their fixed destination and share the live coordinator's disable-generation guard.

### Reports and overlay

PNG reports show a themed title with uploaded icon, session name, optional Lounge ID, and gross room points out of 984. The cumulative graph uses net points on an automatically scaled Y axis and race/course text labels on the X axis. A team absent from a confirmed race contributes zero. A pending race makes the cumulative total unknown from that point, so the line stops until confirmation. Penalties apply starting at their linked race.

Team cards show rank, name, net total, gap, and members with individual net scores and overall ranks. Support unassigned players and arbitrary team sizes. PNG and overlay gaps use `-x / -y`, points behind the team immediately above and first place. Ties show zero, and the first row has no gap. Keep the short above/leader key in the PNG; omit the old membership footer and confirmed-race subtitle.

The optional PNG finishing-position grid uses the selected team's current members and corrected race places, with up to three columns. Pending/unknown places stay explicit. Preview selection does not change the global overlay highlight.

The overlay serves `/overlay` and polls the smaller `/api/overlay` response. It can show teams or players, retain all competitors while highlighting one team, and alternate card brightness without changing alpha. Team movement uses competition ranks and current net scores compared with totals before the latest first-confirmed race. Use saved input order and the same current roster on both sides. Pending races and corrections to older races do not advance the baseline. First-race standings have no arrow; reloads preserve movement.

### Worldwides mode

The header selector switches between Mogi and WW without starting capture. Reject a switch during capture startup, running capture, import, upload or failed resource cleanup. Keep the selected session and history independently in each mode, and persist the selected mode. WW uses a separate database and capture/import state under `ww/`; UUID screenshot evidence and private OBS connection settings remain shared. Stale mode-bound writes must not operate on the other mode.

WW tracks the highlighted local player, not arbitrary opponents. The display name belongs to the session and requires no name recognition. Reuse the seven dashboard tabs and acquisition/import controls, but replace team/roster standings and twelve-identity review with VR statistics and a single-player result editor. Hide team, penalty and Lounge actions. Positions allow 1..24 and display without a lobby denominator. Hide the lobby-size editor and help text; preserve old saved counts on edits unless a corrected position contradicts the hidden count, then clear it in the new revision.

Stable final VR and finishing position update automatically. The initial rating shown beside a signed change is not yet final. Require a settled final observation corroborated by another final read or repeated rating-plus-remaining-change totals from the same result window. Validate any observed before/change pair against the final value. Reject ambiguous reads. Live OBS/camera capture assumes consecutive results within the same session and derives each new result's change from the latest preceding recorded final VR. Persist that derivation choice on new live revisions so earlier corrections/deletions update dependent changes; manual corrections override it. The first live result needs its own observed before/change or remains unknown. Video imports and existing unflagged revisions stay conservative: a cut result may contribute known final VR/position while its starting VR/change remain unknown, without adjacent-race inference. Scrolling a highlighted row off-screen is not a new race. Corrections replace results and use revision checks; an in-flight automatic result must not overwrite a manual correction.

Show current, starting, peak and lowest observed VR, with a graph that breaks at unknown or unproven transitions. Include known before-race observations in extrema without inventing a starting value. The latest observed final VR remains current even when a later race is pending. Show total session change only when all logged races have known changes. Course tables provide race and known-change counts, mean known VR change and mean raw finishing position, with current-session/all-WW scope. Use the labels **Mean position** and **Mean VR change** in both Standings and PNG course tables; the PNG heading is **Best courses by mean position**. Sort courses by ascending mean finishing position, breaking ties by course name, in the dashboard and PNG reports. Do not imply room-size normalization.

The same OBS overlay URL follows the active mode. WW offers independent graph/current/peak/lowest and last-10-race VR-change toggles, defaulting on, alongside shared visibility, geometry, opacity and motion settings. The rolling change sums the latest up to ten recorded results in the current session, excluding pending races. Show the actual number of results used. An empty window or any unknown change makes the rolling total Unknown; never replace missing changes with zero. Corrections and deletions recalculate the window. The character toggle defaults on and is independent of the shared header-text toggle: the icon can remain with the title/details hidden, or vice versa. When both are off, hide the header container. Mogi keeps its existing combined header/icon behavior. Keep the player label Unicode-capable and use neutral Unicode-capable fonts for labels. Preserve transparent backgrounds and last-good state. Show the current session's detected track opposite the player name while that capture detection exists, never by falling back to the last history entry; wrap safely in narrow layouts. WW folder PNG exports use VR history, extrema, finishing positions and course statistics with explicit unknowns. The race table contains course, place, VR change and final VR, without lobby size. Always show course names as text, with optional local course thumbnails and no bundled artwork. Omit the graph-gap helper, averages helper and player-tracking footer without changing their underlying calculations. Unloaded folders export their own data and label; the preview has no team selector. Mogi PNGs retain their existing content.

Course names, evidence, recording-relative timing and chapter correction/export follow the existing rules. CPU highlight/digit recognition lives separately from Mogi eligibility and never starts an optional name model. The supplied edited videos establish regression cases, not universal recognition or real OBS/camera validation. See [WW operation and validation limits](docs/ww.md).

## Mogi FFA

[Mogi FFA (#64)](https://gitlab.sandal.tech/ebuttonsdude/mkw-livesplit-vos/-/work_items/64) is a separate UI choice alongside Mogi Teams and Worldwides. It reuses Mogi scoring, capture/import, confirmation, correction and penalty logic rather than creating synthetic teams. FFA has its own saved data context and history under `ffa/`, with a persisted session format. Switching modes resumes that mode's session; existing team sessions are never converted.

FFA requires twelve distinct finishers and the same 82 points per race. Capture results remain pending until explicitly confirmed. Team creation, assignments and name-initial bootstrap are unavailable. The dashboard, OBS overlay, folder PNG and text/Markdown exports present individual players, with Unicode-capable names, player-ID highlighting, net points and gaps. Player charts replace team charts in FFA PNGs. The shared OBS overlay URL follows the active mode, retaining transparency, reduced motion and last-good state.

Player movement follows Mogi's latest first-confirmed race baseline with competition ranks and no first-race arrow. Mode/session guards reject stale edits and switching while capture/import/upload work is active. FFA exports use the selected folder's format and roster, not inferred one-player teams. Teams and WW retain their existing behavior and histories.

## Design principles

1. Make score provenance visible. A pending screenshot is evidence to review, not a score.
2. Keep the operator in control. Discovery, source selection, and settings edits never start capture or change OBS outputs.
3. Protect capture throughput. Keep expensive recognition, model setup, device reads, response serialization, and socket writes outside the shared application lock. Use short locked sections for consistent state changes.
4. Preserve context. Refreshes must not replace focused inputs, reset file selections, discard drafts, or move the page unexpectedly.
5. Fail visibly without discarding useful state. Keep last-good standings, review queues, and captured evidence when a connection or background job fails.
6. Do not guess missing data. Unknown recording offsets remain unknown. Proven partial finishing rows may prefill manual review; missing positions and identities remain blank and cannot score.

## Visual language

[`themes.py`](mkw_tracker/themes.py) owns 50 character palettes, with Mario as the default. Shared values are `bg`, `panel`, `input`, `text`, `muted`, `line`, `accent`, `accent_text`, `danger`, `warning`, and `team_colors`. Dashboard CSS maps these onto its existing custom properties; do not add a competing theme system. A team's optional `#rrggbb` override follows it across rename, restart, and session loading. Automatic color restores the theme-derived value.

Use dark tinted backgrounds, bright accents, rounded cards, stripe/checker motifs, and aligned tabular scores. The dashboard uses system sans-serif, an 8 px spacing base, 24 px desktop panel padding, and 12 px panel corners. OBS cards use 14 px corners. Names can wrap rather than disappear behind score columns.

Use neutral system sans-serif on dashboard controls. Settings → Overlay & PNG font offers local system TTF/OTF files and user-provided files in `data_dir/fonts/`, with an editable mixed-script preview, Save, Reload fonts and Reset to default. The shared `report_font` setting is a runtime basename or opaque system ID, never an absolute path. A missing selection remains saved but falls back safely; an empty string selects the default. Mogi, FFA and WW share this preference for all PNG and overlay names, labels and scores, without changing layouts or transparent backgrounds.

The browser loads the chosen file through the same-origin allowlisted font endpoint with swap behavior and retains native Unicode fallback. PNGs measure and draw identical fallback runs on a shared baseline. Combining marks, variation selectors, emoji modifiers, flag pairs and joined sequences stay intact during font fallback and wrapping. Missing glyphs fall back to installed fonts; if no font covers a sequence, preserve the text and its missing-glyph marker rather than dropping a name or aborting the export. Font coverage is checked before opening native faces. Per-size face/source caches and the shared digest-only coverage cache have bounded residency; oversized fonts remain transient without splitting shaping runs. These cache budgets are not native-RSS guarantees. Fonts are never bundled or downloaded. Local font binaries remain runtime data; the API excludes filesystem paths and rejects traversal/symlink escapes.

A saved theme may include a user-supplied icon shown consistently in dashboard, overlay and PNG headers. Without an icon, hide its slot cleanly. Course names remain visible text; optional user-uploaded local thumbnails must not replace the labels. Respect reduced motion.

## Components

Reuse the existing native buttons, labeled inputs/selects, semantic score tables, status badges, notices, inline errors, folder disclosures, race editors, and confirmation prompts. Right-align score columns and distinguish pending results from confirmed scores.

- Capture review pairs the screenshot with twelve editable identities. The first-review team bootstrap is explained in the card. Show the current pending race only until a results candidate replaces it. Sparse candidates retain actual observed positions and label their observed count. Fuller single-frame captures update only untouched fields; typed/cleared names and explicit identity choices remain operator-owned.
- History folders expose load, rename, Lounge ID, PNG preview/download, and delete without requiring the folder to be loaded. Lounge links use only the fixed MKCentral numeric table route.
- PNG preview uses a native dialog with loading/error feedback, a team selector, full-size link, and explicit Copy image. Preview, clipboard, and full-size view use the same fetched PNG snapshot. Cancel earlier loads and revoke blob URLs on replacement/close. Explain the right-click fallback when clipboard access is unavailable.
- Overlay preferences are partial validated updates, not replacements for unrelated settings. Reject unencodable text before changing memory or saved settings. Overlay titles are limited to 100 characters without controls.

[`settings.py`](mkw_tracker/settings.py) owns overlay defaults and bounds:

| Preference | Default | Range or choices |
| --- | --- | --- |
| Layout | Teams | Teams or players |
| Size | 560 x 600 px | Width 240..1920, height 40..2160 |
| Font size | 22 px | 12..48, capped for narrow layouts |
| Panel opacity | 0.85 | 0..1 |
| Overflow speed | 30 px/s | 5..120 |
| Visibility effect | Bouncy slide, left, 400 ms | Slide, fade, checkered flag wipe, pixel dissolve, pop zoom, 3D flip, circle iris, curtain wipe; left/right edge, 0..5000 ms |
| Automatic visibility | Off | Hide at live recognized intro; show at accepted results |

Header, members, gaps, animation, overflow scrolling, and alternating backgrounds default on. Highlight defaults to none. Per-source show/hide/toggle state resets to shown on application restart; display preferences persist. Automatic visibility is opt-in and uses in-memory live capture edges in Mogi Teams, FFA and WW. Hide once when a recognized course intro arrives, show once when accepted results are available, including proven partial Mogi review rows (Mogi results remain pending until confirmed, unless YOLO was explicitly enabled). Layout-only/suspected results, manual score edits, and imported recordings do not trigger it. A manual show/hide/toggle lasts until the next detected edge; fuller captures of the same partial review do not emit another result edge. Enabling waits for the next event; restart never replays persisted capture latches. Ignore events owned by another session. Hiding must preserve card DOM and scroll progress. Zero-duration, disabled animation and reduced-motion transitions switch immediately. Slides overshoot slightly on entry and ease out without bounce when hiding. Patterned effects clip the real scoreboard with neutral geometric tiles; no artwork or dependencies. Interrupted effects settle to the latest visibility state, including a live reduced-motion change; first-load hidden overlays never flash.

Overlay visibility POSTs accept an empty body or JSON `{}` without a tracker-mode tag in either Mogi or Worldwides. They control the backward-compatible default source, not session data. Named-source routes and independent visibility are described below. Explicit mode tags retain stale-mode checks, and all Host/origin checks still apply. Other mutation routes retain their JSON-body and mode-ownership requirements.

### Custom theme library

Keep built-in palette colors as immutable presets, without bundled portraits. Users can upload their own PNG/JPEG/WebP icon, edit existing UI color tokens and twelve standings colors, and save a named theme with its icon. Image-derived suggestions are previews applied only by explicit action; they must not overwrite unsaved edits on upload. Saved themes persist under the local data directory, not source control. Unmodified built-ins use Save as copy; custom themes support update and confirmed deletion with a safe active-theme fallback. Explicit migration overwrite may store a local override under a built-in ID, retaining one effective picker entry without modifying source presets. Existing overrides support editing and confirmed Restore built-in; restoring keeps the selected ID and sessions. Overrides have their own bounded built-in slots, independent of the fifty custom slots, within the shared library byte budget.

Decode and normalize uploads with strict byte/dimension limits; reject active formats such as SVG and malformed data. Never expose local paths. All modes and PNG/OBS outputs resolve the same saved theme. Provide keyboard-accessible labeled controls, a preview, clear errors, and readable text/contrast choices. Color suggestion does not assert universal accessibility.

## Accessibility

Target WCAG AA contrast, without claiming a completed accessibility audit. Use semantic controls/table headers, labels, visible keyboard focus, and live status/error regions. Do not rely on color alone for capture status, deductions, or rank movement. Movement arrows include direction and magnitude in their accessible labels.

Tabs use `tablist`, `tab`, `tabpanel`, selected state, linked labels/controls, and roving tabindex. Arrow keys and Home/End move between tabs. Hide inactive panels. Folder disclosures remain keyboard-operable. The PNG dialog closes with Escape, restores focus, and pauses dashboard redraws while open.

Honor `prefers-reduced-motion` for panel entrances, score changes, slide visibility, and overflow scrolling. Keep controls operable without hover. Preserve focused inputs during polling and render user text safely rather than inserting it as HTML.

## Responsive behavior

Above 1000 px, use the left tab column and side-by-side team/player standings. At 1000 px and below, tabs wrap horizontally, standings stack, and settings use two columns. At 600 px and below, settings, placements, and race forms become single-column; panel padding drops to 16 px and character portraits shrink.

Tables scroll within their containers. Long names, paths, notices, and course titles must not expand the whole viewport. OBS size is independent of dashboard breakpoints. Its page/body stay transparent, only cards have configurable opacity, and font size is capped to keep rank/name/dual-gap columns visible in narrow sources.

## Interaction states

Empty screens explain manual entry or connecting capture. Loading/errors retain useful existing data. Hide empty error boxes. Disable the operation in flight rather than unrelated controls. Background polling updates capture/import progress even when a draft prevents a full redraw; unchanged overlay responses must not rebuild cards or restart scrolling.

Player, team, and manual-race drafts belong to the session displayed when editing begins. Session changes in another tab must not retarget them. Creation requests carry that context and reject stale or missing context inside the write transaction. Rejection preserves the draft; Refresh does not silently reassign it. Load its original session to continue, or reload the page and re-enter it for the current session. Internal capture/import writes retain their explicit session ownership.

Capture distinguishes disconnected, starting/model loading, connected, stopping, and cleanup failure. Show the actual name engine and any Tesseract fallback. Source/mode switches, imports, and uploads remain mutually exclusive until resource cleanup completes. Failed cleanup retains ownership and blocks new work rather than pretending the device/model is free. An upload reserves that exclusion for its whole transfer and handoff to import.

Imports show progress, cancellation, failure, and completion, keep their original destination session, and require review before scoring unless YOLO is enabled. Cancelling preserves existing reviews and chapters. Local-path input avoids a browser copy for large files. The recommended import interval is 0.5 seconds, independently configurable from live capture.

Confirm destructive actions with their name and scope. Session and confirmed-race deletion require idle capture/import/upload resources. Pending-race deletion may discard a false start during capture/import; prune review state and invalidate in-flight recognition so it cannot recreate the race. Deleting sessions/races removes database records, not original videos or unrelated evidence files.

Video cleanup deletes only confirmed, inactive, app-managed uploaded copies. Never follow symlinks or delete originals imported by path. Protect active uploads/imports and report partial failures. The default age is 30 days; zero means all inactive uploads. Deleting a copy does not delete results or screenshot evidence.

## Content voice

Use short factual labels such as Player, Team, Points, Race, and Needs review. Distinguish observed, suspected, confirmed, and manually entered results. Say Connected only when a real adapter confirms it. Describe demo data as manually verified references, not OCR output. Explain what failed and what remains saved; do not promise recovery of unsampled frames.

## Implementation constraints

### Local service and data

Use Python, SQLite, and vanilla HTML/CSS/JavaScript without frontend dependencies. Pillow renders PNGs with local fonts and assets. Keep bundled attribution/provenance, small still fixtures, and documentation in source packages. Keep videos, credentials, environments, model weights, real databases, and runtime state out of Git.

Dashboard state retains full history for existing editors, folder controls and WW statistics. Read race collections in batches rather than querying each race separately. Overlay and text outputs read only the active session; a PNG reads only its requested session without changing the active session or its highlight ownership. Single-entity API checks and recording chapter export use narrow, uncached queries. Capture/import bookkeeping must not load lifetime history per frame. Preserve the caller's lock discipline and consistent read transactions; these changes do not enable WAL or split the application lock.

Update text standings after committed writes, including background WW capture/import results, and after mode or output-format changes. Dashboard and download GET requests must not write output files. Failed output writes leave committed data intact and retry after one second without replaying the mutation. Stop and join pending retries before closing the stores. Keep the error visible until a write succeeds. Log database-read failures without undoing committed changes; only retry transient database lock failures automatically. No full-state cache, ETag, compression or history DOM redesign is required by this read-path change.

The HTTP listener defaults to `127.0.0.1`; an explicit operator-selected host can enable network access. Preserve configured-host/destination checks and same-origin writes. These checks are not authentication. There is no login or TLS, so every reachable client can read dashboard data and invoke mutations, including capture with saved OBS credentials. Overlay clients are not read-only. Keep network commands and warnings in the [FAQ](docs/faq.md) and [user guide](docs/user-guide.md), and do not change firewall/router settings automatically.

OBS WebSocket destinations remain loopback-only relative to the server, regardless of the HTTP bind address. Save successful connection details separately in an atomic owner-only `0600` file, never public settings. Blank passwords reuse the saved value; Forget wins over an in-flight save. Public metadata exposes URL, password-saved state, and warnings, never the password. Validate inputs before connection or persistence.

### Capture and recognition

The dashboard defaults to OBS WebSocket screenshots with Automatic name recognition. Automatic prefers installed Qwen3-VL-8B BF16 with at least 22 GiB of free GPU memory, then PaddleOCR, then Tesseract. It does not automatically select a quantized profile. Camera input remains an explicit alternative. Capture starts only through the explicit Start control or legacy CLI source option. Camera discovery uses non-streaming V4L2 QUERYCAP, excludes metadata/output-only devices, and reports inaccessible devices. Device reads/releases belong to their owning worker; never release concurrently with a blocking read. OBS screenshot mode uses the exact source/scene and recording clock without opening a camera or changing OBS scenes/recordings. Reserve OBS Virtual Camera for Discord when needed.

Live capture and video import share five explicit CUDA Qwen choices: Qwen3-VL-4B NF4, Qwen3-VL-8B BF16, INT8 and NVFP4, and Qwen3.6-27B NVFP4. Selector labels show observed peak process VRAM of 4.83, 17.48, 11.73, 8.74 and 22.98 GiB respectively. Help text identifies the short twelve-name RTX 5090 benchmark and distinguishes these measurements from total GPU use or guaranteed requirements. Preserve selections and warning text across polling without starting a reader. Status shows the actual profile or fallback, not a generic Qwen label.

The separate **Qwen3-VL-4B GGUF Q4_K_M** choice is the recommended Qwen starting point for cross-vendor compatibility and smaller downloads, not a guaranteed accuracy or speed improvement over NF4. Keep NF4 available as an NVIDIA CUDA alternative and never migrate existing selections automatically. GGUF uses the official Qwen GGUF weights and F16 projector with checksum-pinned llama.cpp b12110 CPU/Vulkan binaries under `.local/`. It requires no PyTorch/CUDA and is never selected by Automatic. The interactive setup adds it after the original seven menu entries and asks for Vulkan or CPU; explicit command-line setup defaults to CPU. Backend changes require explicit setup while capture/imports are stopped, not runtime downloads or silent backend fallback. Automated binaries cover x64 Windows and modern glibc Linux. Vulkan requires a compatible vendor driver. RX 6800 XT and Arc B580 are intended targets, not validation evidence; minimum required memory and live throughput remain unvalidated; a small paired quality check is recorded in the recognition reference, not proof of broad NF4 parity. Never label the 3.33 GB download as a RAM/VRAM requirement. A single-row CPU and RX 7900 XTX partial-Vulkan smoke passed on 2026-11-08 with at least 22.27 GiB free VRAM on the runner. This is integration evidence only, not full-offload or recognition-quality validation. Vulkan’s 4 GiB fit hint is advisory, never a safety boundary.

The optional **External vision endpoint** reader is selected explicitly in live capture or imports, never by Automatic or managed-reader setup. Settings → Experimental holds an HTTP(S) base URL ending /v1 or full chat-completions URL, model ID and private optional API key. Recommend local/self-hosted or trusted second-computer use. All hostname (except pinned localhost) and non-loopback numeric test/recognition requires explicit image-disclosure/cost consent. URL/model edits clear consent and do not carry the old key forward. Configuration changes invalidate existing readers; stale saves cannot undo Forget. `external-vision.json` uses the private owner-only file pattern and must be excluded from backup exports and logs. Public state contains configuration metadata only, never the key.

Numeric destinations bypass DNS (localhost is fixed to 127.0.0.1). Other hostnames require explicit consent and verified HTTPS using the original hostname for SNI/certificate verification and Host. Pin each connection to one checked numeric DNS answer; reject an entire answer set containing unsafe peers, including DNS-to-loopback. Permit HTTP only on literal loopback/RFC1918/ULA; reject mapped/scoped/special addresses. Never use proxies or redirects or accept embedded URL credentials/query/fragment. DNS, connection, TLS and response share the request deadline; a timed-out/cancelled OS resolver may finish in the background but cannot transmit images, and only one resolver thread may be outstanding. OBS endpoint checks and managed GGUF lifecycle are unchanged; the external reader never launches or tears down a server.

Transmit only name-crop PNG data URLs, model/request parameters and a short transcription prompt. Downscale to at most 512×128, at most 256 KiB per PNG, less than 351 KiB JSON per request, 16 KiB response body, 96 output tokens, up to twelve sequential requests per attempt. Each request has a 30-second deadline (TCP connect and TLS handshake each capped at two seconds); an attempt has 300 seconds. Recognition may repeat, not once per race. No automatic retries; a failed external reader stays closed until explicitly recreated. Startup does not send a warmup image. Test connection sends one freshly generated random word image, never real capture, excludes the expected word from the prompt and compares the transcription exactly. This is a limited compatibility check, not proof of Unicode/name accuracy.

Cancellation interrupts local sockets without server-process control; remote computation/cost may continue. UI warnings cover disclosure, repeated cloud costs, HTTP visibility, CPU/network speed and user-managed security/resources/availability. Preserve baseline names/review on failures, unique exact matching, unavailable generative confidence and explicit normal Mogi confirmation. Configuration save, Forget and test/cancel actions must remain responsive without holding the application lock across network requests. Availability-disabled endpoint buttons must not block background standings refresh. Fence responses by draft/action/config revision so older saves/tests cannot replace newer edits or Forget/Cancel state.

NVFP4 choices display a hardware warning. This CUDA-only implementation supports the tested SM120/SM121 Blackwell backend and dedicated CUDA 13 runtime, not RTX 4070 or Radeon GPUs. RTX 5070 architecture support does not guarantee enough free VRAM. The 4B NF4 reader uses bitsandbytes on BF16-capable NVIDIA CUDA hardware, including Ada, and does not need Blackwell or NVFP4 kernels. Its RTX 5090 benchmark is not evidence of actual RTX 4060 or live OBS performance. Workers check compatibility and conservative free-memory bounds before model load: 6.5 GiB for 4B NF4, 22 GiB for 8B BF16, 14 GiB for INT8, 11 GiB for 8B NVFP4 and 25 GiB for 27B NVFP4. Optional setup keeps the existing BF16 environment separate from NF4, INT8 and NVFP4 dependencies; quantized loading never silently offloads weights to CPU or disk. See [recognition setup](docs/technical/recognition.md#qwen-gpu-readers) for storage and runtime details.

Preserve native-resolution JPEG quality 95 for OBS screenshot transfer, then save decoded evidence as PNG. Live capture defaults to a 250 ms minimum serial cycle, including acquisition and recognition. Do not add catch-up bursts, overlapping recognition, or a second per-frame queue. Report requested interval and last acquisition/recognition/estimated cycle times without presenting them as guaranteed FPS. Do not reconfigure an already-running capture.

Bound HTTP connection handling and shutdown so idle/slow clients cannot hold the shared application lock or indefinitely delay shutdown. Moving output work outside that lock must preserve response consistency and safe database shutdown. Measure capture cadence under concurrent clients with mocked adapters before claiming a performance fix; real OBS throughput requires separate live validation.

HTTP headers, JSON bodies, and response writes use five-second inactivity timeouts. Video uploads retain their longer transfer timeout. Server shutdown interrupts accepted client sockets, including continuously trickling requests, then joins request threads before closing application data. HTTP connection bookkeeping uses its own lock, not the capture lock. These deadlines do not alter OBS WebSocket timeouts or screenshot cadence.

Strict full-table recognition supports the tested neutral and team-colored twelve-player layouts. Require finishing phase, all twelve proven rank glyphs, and at least three distinct +points rows before neural names. This path rejects partial, reordered, consumed-points, summary, and gameplay screens. Preserve strict template thresholds and highlighted-podium handling. Stop row OCR when an unproven rank already makes the frame ineligible. Pre-race detection needs horizontal panel continuity, not merely repeated colored slots. Rainbow Road's bounded alternate crop must still match the known course vocabulary.

A separate ordinary-review fallback accepts partial finishing tables only with at least three coherent, unique, ordered rank/+points anchors. Infer row geometry, never a missing rank: prove each returned rank independently at the unchanged glyph threshold and crop names at its actual screenshot position. The +points amounts and cumulative scores are not scoring inputs. Unknown slots remain absent; no placeholders or automatic confirmation are added. This conservative white-rank guard may reject partial team panels with exclusively dark ranks. Keep a partial candidate armed for strictly more observed rows from a single later frame, retaining its stable review ID and screenshot evidence; never union different frames. Full results close the latch. Confirmation, dismissal and manual race scoring fence in-flight upgrades, including imports and restart recovery. Enabling YOLO does not retroactively auto-confirm a partial manual review. Worldwides and the existing YOLO reader remain unchanged.

Store original pre-race and finishing screenshots. A rejected table-like frame may save at most one suspected-results image for the current known pending race, including across restarts. Require independent table evidence and current ownership; never create a race, review, or inferred placements through this fallback. Prefer later verified results for display. Warn that suspected evidence can be partial or cumulative standings.

Imports overlap ordered recognition with one prefetched decoded frame and one model. The decoder worker owns video operations. Recording-relative timestamps come from a validated OBS clock or imported file, never camera elapsed time. Unknown offsets stay editable and absent from chapter export until corrected.

### Validation

Follow the [contribution checks](docs/technical/contributing.md#validation) with temporary databases, mocked hardware/clipboard boundaries, and read-only original recordings. Run both browser acceptance modules for UI changes and inspect the relevant example images. Mocked browser or OBS protocol tests do not prove real OBS/CEF, camera, GPU, or continuous-capture behavior. Record those gaps explicitly. No tests may restart or reconfigure the operator's live tracker or OBS.

## Open questions

- [ ] Recognition maintainer: validate unseen names/layouts and longer sessions. Strict frame eligibility and human confirmation remain the default; generative name confidence is unavailable.
- [ ] Integration maintainer: validate continuous OBS screenshot cadence with the operator's OBS/source/auth setup and concurrent dashboard clients. Mock tests establish scheduling and lock behavior, not live throughput.
- [ ] Integration maintainer: verify recording splits, pauses, and exported chapters against actual recordings. Keep uncertain file-local offsets editable.
- [ ] UI maintainer: audit keyboard/screen-reader use and contrast across character palettes and custom team colors. Existing semantic controls and browser tests are not a complete accessibility audit.

## Beginner documentation website

The documentation is a separate static Hugo/Hextra website; it never hosts the
tracker, its API, or saved user data. GitLab Pages is restricted to project
members until a deliberate publication decision. A configurable base URL keeps
the same content portable to GitHub Pages and project subpaths.

### Information architecture and content voice

The README explains what the app does, shows real screenshots with fictional
data, and provides a short installation route for Arch, Fedora, Ubuntu, native
Windows, and WSL. Everyday documentation uses brief sentences, numbered actions,
visible next steps, and an expected result after setup. Platform guides live under
`docs/install/`. Linux uses accessible horizontal distro tabs for prerequisites
and distro-specific follow-up notes, with common tracker setup commands outside
the tabs. WSL documents the maintainer-tested Windows 11 mirrored-networking
route with NVIDIA NF4; that does not establish NVFP4 support. Explain a term before
using it. Do not label instructions as specifically for a diagnosis; reduce
reading effort for everyone. Long compatibility records, model internals,
building and contributing belong under `docs/technical/`, not the beginner path.

### Website visual language and accessibility

Use the Hextra documentation layout inspired by the Hyprland wiki: persistent
section navigation, a readable central column, page contents on wider screens,
search, and a website theme picker. The picker reuses all 50 exact built-in dark
character palettes from themes.py at build time, plus a separate Standard light
option. Mario is the default; the browser remembers the selection across pages.
The picker lives in the header beside Start here; narrow screens use a second
header row. The documentation uses the full viewport width: fixed-width edge
sidebars flank a fluid center column, without a fixed article-width cap. Desktop
sidebar backgrounds cover the viewport below the header and align at the content
end. The scroll-to-top area uses its panel color without an independent shadow.
Custom runtime themes and uploaded artwork are never published. Keep the default text contrast and keyboard focus
indicators, provide a skip link and mobile navigation, and respect reduced
motion. Use restrained race-night accent colors rather than decorative game
artwork. No analytics, autoplay, or animated marketing content. Website build
dependencies are separate from tracker installation.

### Guided setup contract

The setup helper reuses the existing dependency installers, not a new packaging
system. Offer all supported neural readers with plain hardware/memory labels plus Skip,
while recommending GGUF Q4_K_M as the Qwen starting point for Vulkan (AMD/Intel/NVIDIA) or CPU. Retain PaddleOCR as the smallest CPU-only option and NF4 as a CUDA alternative. This recommendation does not select or install a reader automatically. Preserve `cpu`, `8gb`,
and `nvfp4` aliases and accept canonical engine IDs. Name
readers are optional but highly recommended. Skipping a reader does not mean
skipping the capture/report dependencies; manual-only setup is separately
available. No system package installation or model download happens silently.
Native Windows rejects both NVFP4 profiles before installation; free GPU memory matters, not just the card label.
Keep advanced app reader options unchanged and explain explicit reader selection
for GGUF/NF4/INT8/NVFP4 because Automatic does not select them. Windows users run
`setupWindows.bat` once (uv prepares Python 3.12) and `startWindows.bat` for
everyday startup. Both anchor paths to their checkout and retain visible errors.
Default Windows Tesseract folders are discovered after PATH; custom folders need
a persistent user PATH or same-terminal launch. Linux users run `sh setupLinux.sh` once and `sh startLinux.sh` for everyday startup; the startup wrapper anchors its checkout, checks the existing executable Python environment, forwards arguments unchanged and execs the foreground tracker so Ctrl+C and exit status propagate. It never installs dependencies or backgrounds the app. Both setup wrappers use the same install/remove/default chooser. Recognized Arch, Fedora, and Ubuntu prerequisites require explicit approval. Setup never starts capture.

Setup stores checkout-scoped defaults and removal markers in `.local/reader-setup.json`, separately from running application settings. Both reader forms initialize once; polling must preserve operator choices. Automatic keeps its existing preference order. Removing a default requires choosing a replacement first. Removal rejects symlink paths and preserves shared assets, external caches, and custom paths. Successful reinstall clears removal markers. Invalid configuration produces a visible warning and safe Tesseract UI default; neural startup fails closed. Close the tracker before removal; setup does not terminate it. Downloadable Linux/Windows
installers remain a separate follow-up issue.

The Recommended setup bundle explicitly installs PaddleOCR followed by GGUF Q4_K_M, offers the GGUF backend choice, and offers GGUF as default only after both succeed. Partial failure preserves the prior default. The bundle is not an engine and cannot be removed or selected as a default as a single reader. Existing menu IDs and Automatic behavior remain unchanged.

### Evidence and boundaries

Documentation screenshots are generated by `tools/docs_screenshots.py` with a
temporary database and fictional names. See `docs/images/README.md` for
provenance. Screenshots are website/repository assets, excluded from image-free
application packages. Platform instructions distinguish tested behavior from
expected compatibility; synthetic browser checks never imply real OBS, GPU
inference, or camera validation.

## Upstream migration

`python -m mkw_tracker.migrate SOURCE` opens a dependency-free numbered terminal selector when stdin is interactive. Show per-category counts/source-byte estimates, temporary database workspace and destination free space; initial categories include sessions/evidence/pictures/fonts, with uploaded videos/models off. Select everything explicitly includes both optional categories; evidence selection requires sessions. Preview is read-only and migration requires a final exact yes; retain `--preview`, `--only`, `--everything`, `--non-interactive` and explicit `--apply` for scripts. EOF/cancellation before confirmation writes no target data. Local transfers do not inherit ZIP aggregate/session/row/database-byte caps: file-backed evidence descriptors retain stat identities, PNGs are verified and copied one at a time, source changes abort, and newly created evidence rolls back with the database transaction. Relational validation uses temporary on-disk schemas; metadata memory remains proportional to record count. ZIP endpoints retain their original bounds. Inventory reports unavailable evidence without blocking unrelated selected categories; selected missing evidence still fails closed. Only actual overwrite choices compute target-session fingerprints. Theme picker count/byte/image budgets never prevent copying original assets or importing sessions; preserve extras with explicit warnings. Source databases are copied into temporary storage before schema upgrades; source files are never modified. Sessions, dependent roster/results/revisions and evidence are validated using explicit table/column allowlists. All incoming identities and evidence names are regenerated, preserving relationships and saved order without replacing target records unless the operator explicitly selects Overwrite for that name conflict. Terminal conflict resolution offers Rename (validated custom name or automatic suffix), Skip and Overwrite; noninteractive application requires an explicit policy for unresolved conflicts. Replacement deletes only the chosen session in the same SQLite transaction as insertion, validates a target-session fingerprint, and redirects its active selection only if that session was active. Failed imports roll back scores and overwritten assets; abort before application leaves data unchanged. Theme names conflict against both built-ins and saved customs. Explicit overwrite prefers the matching built-in ID, removes exact-same-name duplicates and remaps a selected duplicate ID; unrelated names remain. The terminal offers overwrite-all scoped only to subsequent themes, and `--theme-conflict` overrides the general file/session policy. `--only pictures` repairs prior duplicate migrations without importing sessions. Runtime overrides preserve saved source theme colors, while legacy static icons use known installed palettes. Pictures become local theme overrides/custom themes or retained course assets; portable fonts use `data_dir/fonts` and a basename `report_font` contract. Preserve any explicit target preference, including the empty default. Videos and portable model files require separate flags; never copy credentials, runtime executables or virtual environments. Model files retain their relative names under the target `.local/models` (or an explicit destination).

## Diagnostics

The launcher configures payload-free component diagnostics under `<data-dir>/logs/`.
Normal files use INFO; `--verbose` adds DEBUG status/timing/progress events without
changing WARNING-and-above stdout output. Startup still prints the dashboard URL
and data folder. Six rotating files separate app/output, HTTP, capture (including
camera/reader readiness), OBS connections, local/external readers and imports. Each retains three backups
at 2 MiB per file. Only fixed events, bounded numeric metrics and safe failure
categories are logged; no raw exceptions, URLs, headers, credentials, image/name
payloads or recording paths. Raw worker stderr is discarded rather than appended to unbounded engine logs.
Root and third-party loggers are not reconfigured.
Existing dashboard operation errors remain unchanged. Demo logs are temporary.

## Credential-free session backups

Settings includes a distinct Session backups panel. Selection lists sessions across all three modes with names and race counts; ZIP exports include only explicit relational session columns and referenced bounded PNG evidence. No private connections, tokens, settings, runtime/model files, videos or unfinished capture queues are exported. The version-1 archive format is documented in the backup guide. Imports validate every field, graph relationship, score revision, archive path/type/size and image before offering a ten-minute, single-use preview token. Confirmation adds fresh identities without replacement, preserves existing active selections, rechecks idle capture/import/upload state under the application lock, and merges all modes transactionally using a disk-backed coordinator. Export queries only selected session subgraphs and enforces manifest, image-count and cumulative-byte limits before loading evidence; ZIP uploads have a two-minute absolute deadline. Version-1 manifests normalize an absent `races.auto_confirmed` to integer zero, preserve 0/1 provenance when supported, and reject other types/values. Invalid archives or failed inserts do not replace existing sessions. Names render as text, not HTML. ZIP upload/export and confirmation retain host/origin checks. Browser tests use synthetic records and images, not OBS or real recordings.


## License and source access

Original project code is GNU AGPLv3, version 3 only, with the narrow optional
NVIDIA inference-reader permission in `LICENSES/NVIDIA-Reader-Exception.txt`.
Keep the full license, grant, attribution and third-party notices in wheel and
source distributions. No dependency binaries, models or environments are bundled.

An always-visible dashboard header link opens the same-origin `/license` page
with copyright, warranty, redistribution, license and public repository links.
Forks must point it at their actual corresponding source. The repository is
public; do not add runtime source archives or expose checkout files, configuration
or credentials. The fixed static route retains Host checks and CSP. OBS output
remains transparent and has no legal watermark; overlay HTML carries a nonvisual
license relation. Remote-only overlay deployments must separately ensure their
users receive a prominent source offer rather than relying on hidden metadata.

The docs site publishes full Hextra, FlexSearch, Tailwind and Heroicons notices
alongside the project legal documents and links them from License & notices.
Keep copyright/license banners when producing site assets. Public/private Pages
repository visibility is accepted; actual Pages audience remains a server-side
setting, never inferred solely from project visibility.

## CI toolchain reuse

Keep dependency downloads outside Git and application archives. A prepare job builds or reuses a runner-local Docker image keyed by declared tracked toolchain inputs; downstream jobs receive a validated immutable image identity, not a mutable tag. Cache the Python environment, pinned Playwright/Chromium and Linux dependencies, checksum-verified OCR data and documentation archives. Tests and Pages builds still receive fresh tracked source in disposable private-PID containers, with no host checkout/credentials/devices/socket mounts. Do not run process tests directly on the limited runner account. `CI_TOOLCHAIN_REBUILD=1` refreshes only this toolchain, without broad image pruning or process cleanup. A warm run skips installation; a cold run still requires network access and may fail. Docs downloads use bounded transient retries, identify the tool/host, reject checksum failures and publish cache entries atomically.


## Local course pictures

Course pictures are runtime-only, shared across all modes in `course-images.json`, never bundled, published to the documentation site or exported in session ZIPs. The default-collapsed Course pictures disclosure lives beneath theme controls. Batch selection conservatively matches complete known names or the forty exact legacy asset IDs from the old app’s textual course-to-file mapping (without executing legacy Python or bundling artwork), permits explicit custom-name associations, leaves unknowns pending, uploads sequentially and reports per-file results. Replacement/deletion is explicit; async responses cannot erase newer drafts. Decode only bounded PNG/JPEG/WebP still images through the existing normalizer, strip metadata and store normalized PNG bytes within a 256-record/32 MiB library. Serve allowlisted IDs through the same-origin API, never arbitrary files or remote URLs. Public metadata contains no image bytes or local paths; cache URL metadata rather than re-decoding pictures for every race on each poll.

Decorate race/capture and WW course-stat snapshots at the application boundary. PNG routes capture matching bytes under the app lock and pass immutable images to renderers; names and scoring stay unchanged. No picture means the existing text-only layout; browser image failure hides the image, not the label. OBS overlay behavior remains unchanged. Migration copies raw originals independently of display conversion, activates exact recognizable filenames and saved associations with runtime > static > retained priority, preserves unknown/oversize sources with warnings, and rolls back the library with other preferences. Course-image conflicts are Skip/Overwrite; general Rename keeps an existing association with a warning. Pictures-only reruns must not reimport sessions. Documentation can provide a user-run download prompt; the application does not fetch or redistribute course artwork.

## Named overlay sources and optional Lounge cards

`/overlay` remains Default; `/overlay/<slug>` selects a persistent named source.
Keep the existing default display settings contract. Named sources snapshot those
options at creation, merge new options from defaults, and own independent runtime
visibility. Themes/fonts can override dashboard choices without changing reports;
block deletion of any theme referenced by a source. Dashboard standings use global
settings, not Default source overrides, and never schedule Lounge requests. All sources follow the active
mode/session. Explicit content assignments support duplicate blocks or an atomic
exclusive move to any source, including Default. Grouped WW blocks retain the
existing finer-grained toggles. No scoring or identity data is copied/moved.

Keep the source editor separate from roster identities. Preserve unsaved drafts,
fence delayed saves by draft revision, and use a fresh profile-list response after
mutations. Server instance/revision metadata prevents an earlier outstanding state
poll from overwriting a successful source save or resetting a newly created source.
Default's legacy settings endpoint remains authoritative for its display options.

Lounge is opt-in per source and never fetched/displayed in WW. Manual usernames,
season and room size are separate from in-game identities. Show canonical remote
identity with accurate season/room scope. Select metrics independently: division,
MMR, numeric leaderboard rank, season, last-up-to-ten-event and derived trailing
seven-day metrics. Positive-MMR event rate is not first-place rate; averages are
whole event scores. Seven-day windows use UTC verification time and complete,
count-reconciled history excluding deleted tables; missing/inconsistent data stays
unknown. Cached windows retain their original as-of time.

Use one bounded asynchronous worker, bounded memory/queue/body/deadline, separate
DNS gate from recognition, fixed-origin HTTPS with public pinned peers and no
redirects/proxies. Never perform network work under the application/capture lock.
Disable/mode changes cancel unneeded queued/in-flight work and fence its results;
failures retain explicitly stale last-good cards without leaking raw errors.
Remote text and aggregate values must survive strict UTF-8/finite JSON serialization.
See [source/Lounge guide](docs/overlay-profiles.md) for limits and official evidence.

### Broadcast Lounge card presentation (T-020)

Use the user's compact badge reference as visual direction, not bundled artwork or a game-style font. Keep the canonical username at left and tabular MMR with a colored uppercase division badge at right; optional statistics share the space between them in the same wrapping flow. Hide technical Lounge IDs, fetched-at times and verification intervals on broadcast cards. Season/room scope belongs in accessible titles and settings, with a compact combined-room qualifier where relevant. Preserve concise loading/unavailable/stale badges without raw errors.

Each source saves Lounge appearance independently: aurora/carbon/theme/transparent background, rainbow/division/accent/none border, pill/rounded shape. Default to a subtle code-generated aurora and thin rainbow pill border. Existing font, opacity and maximum source width apply. No image downloads, external assets, decorative game fonts or new dependencies. Transparent backgrounds must remain transparent inside their border.

Optional metrics use one content-sized flex flow, never separate grid rows by time window. Each bubble carries a short scope (SZN, L10 or the actual recent count, 7D); ALL marks legacy combined-room stats. Bubbles expand to share leftover line width and wrap only when content needs it. Keep name, metric bubbles and rating in that same flow; no empty hero row above metrics. Cards shrink to an identity-only badge or grow with selected content. Use arrows plus signed values for changes, not color alone. Positive-MMR event percentages may be labeled Win %, with truthful scope and accessible explanations; they are not individual-race/first-place win rates. Compute recent percentage only from valid positive/non-positive counts with total1..10; unknown/empty data remains Unknown. Weekly time windows stay in data, not visible timestamps. Preserve safe text, Unicode, selected-field hiding, multiple-player stacking, last-good failure behavior and reduced motion. Narrow240px sources and long names wrap without horizontal overflow.

### Inline form and size guidance (T-021)

Form is opt-in and shows up to five independently validated verified-event outcomes, oldest left/newest right; positive MMR is W, zero/negative is L. Green/red markers also carry visible W/L labels. Never derive a sequence from aggregate wins/losses. Reject incomplete/malformed/deleted/ambiguous histories rather than invent a sequence; empty history remains No events. Legacy form uses the same combined-room scope as season/recent stats.

Offer an estimated compact Lounge width/height from current unsaved fields, usernames/count and font size. No extra network fetches, automatic saves or OBS reconfiguration. An explicit Use recommended size changes display-form drafts only, within width240..1920 and height40..2160 limits (lower height floor accommodates thin strips; defaults stay unchanged); display Save remains required. Mixed-content sources retain a clearly Lounge-only estimate, with apply disabled to avoid undersizing other blocks. Estimates must disclose font/data variability. Preserve source/poll/save fences and hidden WW behavior.

Source font selection uses the existing available local-font catalog, with an explicit Use dashboard font entry. Preserve missing saved IDs as labeled unavailable choices rather than silently changing them; selection never changes the dashboard/PNG font. Reuse source draft/save/poll fences.

### Overlays workspace and confirmation evidence (T-021 follow-ups)

Move source/content/display/Lounge/OBS URL and text-output controls from Settings into a dedicated accessible Overlays tab. Keep theme/library, shared PNG font, backups and Experimental settings in Settings. Preserve seven-tab keyboard navigation, saved active tab, capture/background work and unsaved source drafts.

Apply & preview validates both source and display drafts, saves them for the captured source and then starts a live same-origin overlay iframe at its real saved pixel dimensions, visually scaled to fit a checkerboard preview. No initial preview fetch; leaving the tab or changing source/mode unloads it. Preview rendering may schedule Lounge requests only under existing saved opt-in, never by merely opening the tab. Successful individual saves refresh an already-started preview. Sequential saves are not an atomic transaction: partial failure must remain explicit, preserve drafts and avoid loading a misleading preview. Dashboard/API/error responses stay unframeable; only successful overlay HTML allows frame-ancestors self.

In manual result editors keep all evidence visible but order course images before suspected and accepted results, with newer accepted screenshots closest to the placement form. The separate full evidence gallery retains results-first order. This is presentation only: no evidence deletion, guessed positions or scoring changes.

### Uploaded Lounge backgrounds and Counter HUD (T-022)

Per-source runtime-only uploaded still backgrounds extend the existing appearance enums: custom/hud background and hud border. A Counter HUD preset selects hud/hud/rounded, recreating the user's lap/coin counter reference in CSS (translucent charcoal, double rim, gloss, bold white text) without game-file assets or bundled fonts. Independent background/border controls remain usable.

Use existing strict image decoding with its default icon size unchanged; backgrounds normalize metadata-free PNG up to1920×1080 from ≤4MiB PNG/JPEG/WebP, ≤4096px/16MP, no animation/SVG. Bound normalized size and library storage too. Persist images separately from profiles, expose only derived same-origin digest URLs, and never accept arbitrary background URLs/paths. Decode outside the application lock, then recheck mode, source lifetime and background revision. Atomic replacement preserves last-good images on error; deletion/recreation cannot resurrect another source's old upload. Cache public image metadata rather than hashing large pixels on polling.

Upload/removal commits image data immediately; selecting custom is an appearance draft until Save/Apply. Preserve async draft/source/mode fences and uploaded assets when switching presets. Source deletion removes its image. Center-cover images are decorative, clipped inside the existing card radius, darkened for contrast, and respect source opacity without dimming text; failed loads use the existing panel fallback. Images remain runtime data, excluded from repository/packages and existing session-only backup/migration flows.
