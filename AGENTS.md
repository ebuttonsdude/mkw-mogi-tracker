# Project instructions

<!-- AUTONOMY DIRECTIVE — DO NOT REMOVE -->
YOU ARE AN AUTONOMOUS CODING AGENT. EXECUTE TASKS TO COMPLETION WITHOUT ASKING FOR PERMISSION.
DO NOT STOP TO ASK "SHOULD I PROCEED?" — PROCEED. DO NOT WAIT FOR CONFIRMATION ON OBVIOUS NEXT STEPS.
IF BLOCKED, TRY AN ALTERNATIVE APPROACH. ONLY ASK WHEN TRULY AMBIGUOUS OR DESTRUCTIVE.
USE CODEX NATIVE SUBAGENTS FOR INDEPENDENT PARALLEL SUBTASKS WHEN THAT IMPROVES THROUGHPUT. THIS IS COMPLEMENTARY TO OMX TEAM MODE.
<!-- END AUTONOMY DIRECTIVE -->

## Scope and architecture

This is a local Mario Kart World scoring and recording-chapter companion for Arch Linux and OBS. It is a Python application with a SQLite store and vanilla HTML/CSS/JavaScript. Read `README.md` for setup and `DESIGN.md` before changing UI behavior.

- `mkw_tracker/store.py`: sessions, roster, placement revisions, penalties and score snapshots.
- `mkw_tracker/server.py`: loopback HTTP API, public state and generated outputs.
- `mkw_tracker/capture.py`, `cameras.py`, `imports.py`: live capture, explicit camera lifecycle and review-only video imports.
- `mkw_tracker/recognition.py`, `neural_ocr.py`: phase/rank checks and optional name readers.
- `mkw_tracker/credentials.py`: private OBS connection persistence, separate from public settings.
- `mkw_tracker/reports.py`, `report_fonts.py`, `themes.py`: PNG/text reports, fonts, character themes and team colors.
- `mkw_tracker/static/`: dashboard, transparent OBS browser overlay and bundled assets.
- `tests/`: unit/API/recognition checks, optional Chromium acceptance and small still-image fixtures.
- `tools/`: dependency/model setup, replay and benchmark scripts.

Keep changes small and preserve existing patterns. Add dependencies only with explicit user approval. For cleanup or refactoring, write a brief plan and add regression coverage before changing uncovered behavior. Use bounded role-specific subagents only when independent work improves throughput; assign separate file ownership and do not revert another agent's edits.

## Scoring and product contracts

- A scored race requires exactly 12 distinct player identities in finishing order. Placement points are `15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1`, totaling 82 per race and 984 for twelve races.
- Never use the game's cumulative score as the source of truth. Captured results remain pending until explicitly confirmed. Pending races contribute no points.
- Corrections replace a race's contribution rather than adding it again. Race-linked penalties reduce net player/team points without changing finishing positions or gross room allocation.
- Every history folder is an independent session with its own roster, races and Lounge ID. Roster assignments determine current team totals and reports; do not imply historical membership snapshots.
- Use stable player IDs. Preserve Unicode names and explicit identity choices; automatically match only a unique exact name, never approximate OCR or team tags.
- Keep course names and recording-relative intro timestamps editable. Unknown recording offsets remain unknown, not capture elapsed time.
- Team movement compares current net standings with totals before the latest first-confirmed race, using saved input order and the same current roster. Pending races and older corrections do not advance that baseline. Ties use competition ranks; first-race standings have no movement arrow.
- PNG and overlay gaps are `-x / -y`, points behind the team immediately above and first place. Preserve transparent overlay backgrounds, reduced-motion behavior and last-good state on connection failures.
- Use neutral system/Unicode report fonts. Do not bundle decorative Nintendo artwork, gameplay fixtures, or game-inspired display fonts. Embedded recognition rank masks are the explicit user-approved exception. Uploaded icons and custom themes are runtime data, never source.

## Data, video and credential safety

- Never commit full video files, including renamed or uppercase-extension recordings. `.gitignore` excludes common formats anywhere, `video-samples/` and `uploads/`. Do not bypass these rules with `git add -f` or Git LFS.
- Keep environments, model weights, browser downloads, databases, credentials, generated output and agent runtime state out of commits. `.local/` is runtime data, not source.
- Small, intentional still-image regression fixtures are allowed under `tests/fixtures/`, with provenance. Prefer those over checking in a video.
- Before a commit, inspect staged paths and sizes and check suspected binaries with `file --mime-type`. Ignore rules do not protect already tracked files. Untrack accidentally added media without deleting local recordings; do not rewrite published history without agreement.
- Never print, commit or return the OBS password through public APIs. Persist it only in the separate owner-only private connection file. An explicit forget action must win over an in-flight save.
- Bind the service to loopback and preserve host/origin checks and safe text rendering.
- Do not restart or reconfigure the user's running tracker, OBS, camera, GPU recognizer or real database for tests. Use temporary databases, mocked adapters and read-only source recordings. Camera discovery must not start a stream.
- Video cleanup may delete only app-managed uploaded copies after explicit confirmation. Never delete originals imported by path. Do not erase evidence, sessions or scored results as a test shortcut.

## Verification

Run the smallest relevant tests first, then the full applicable checks. With project-local dependencies installed:

```sh
.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
uvx ruff check mkw_tracker tests tools
node --check mkw_tracker/static/app.js
node --check mkw_tracker/static/overlay.js
.venv/bin/python -m compileall -q mkw_tracker tests tools
```

For UI changes, run both optional Chromium acceptance modules in a local environment with Playwright, Pillow and the capture dependencies. `PLAYWRIGHT_BROWSERS_PATH=.local/browsers` keeps browser downloads local:

```sh
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_smoke
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_overlay
```

Use a different local Python environment if that is where Playwright is installed. Mock clipboard writes and capture inputs rather than changing the user's real clipboard or hardware. Original video files are optional and remain excluded; tests depending on them should skip when absent. Use `uv build` to check wheel/source packaging when changing bundled assets or packaging rules.

Report what changed, fresh verification evidence and any remaining gaps. Do not claim real OBS/CEF or live-capture validation from a mocked browser check. Update README and DESIGN when behavior changes. Commit and push normally, never force, unless explicitly authorized.

## Durable project memory (.agent/)

Conversation context is temporary: it may compact, reset, or be abandoned at any time. The files under `.agent/` are the durable project memory and the source of truth for ongoing work; chat history is not authoritative. Kilo Code-specific rules live in `.kilo/rules/project-memory.md`.

`.agent/` is machine-local, ignored by Git, and not required to run, test, or build the app. Keep shared product and architecture guidance in tracked documentation such as `AGENTS.md` and `DESIGN.md`. Back up local memory separately if needed; it will not follow a clone. The memory routines below apply when `.agent/` is initialized. On a fresh checkout without it, use the tracked project documentation and current task instructions; missing local memory must not block development. The memory helper scripts require initialized local files.

Before starting work:
1. Read `.agent/state.json`, `.agent/tasks.md`, and `.agent/decisions.md`.
2. Skim `.agent/repo-map.md` at a high level.
3. Identify the active task (`current_task_id`) before editing code; if none is set, select or create one in `.agent/tasks.md` first.

While working:
- Load only files relevant to the active task.
- Keep tasks small and verifiable.
- Update `.agent/tasks.md` whenever a task status changes (prefer `python3 scripts/project_memory.py new-task|start|block|complete`).
- Update `.agent/state.json` whenever current progress, next actions, key files, risks, or commands change; it must remain valid JSON after every update.
- Record design, architecture, product, or workflow decisions in `.agent/decisions.md`.
- Update `.agent/repo-map.md` when module structure, entrypoints, data flow, or important file responsibilities change (rerun `python3 scripts/build_repo_map.py`).
- Append meaningful progress to `.agent/session-log.md`; append only, never rewrite old entries.
- Record failures, bugs, and suspicious behavior in `.agent/errors.md`.

A task is not complete until the code, tests/verification, task list, state file, and relevant docs are all updated. Before ending a session, leave enough state for a fresh context to continue without reading the old chat; use `.agent/handoff-template.md` when handing off mid-task.

### Session Start Routine
1. Run `python3 scripts/project_memory.py status` (or read `.agent/state.json`).
2. Read `.agent/tasks.md` and `.agent/decisions.md`; skim `.agent/repo-map.md`.
3. Confirm the active task ID; if it is ambiguous, set it in state before editing code.

### Session End Routine
1. Update `.agent/tasks.md` statuses for everything that changed.
2. Record progress, next actions, key files, and risks in `.agent/state.json` (via `scripts/project_memory.py note` or equivalent), then run `python3 scripts/project_memory.py validate`.
3. Append a session-log entry; fill the handoff template when work continues in another session.
4. Verify a fresh context could resume from the files alone.

<!-- DESKTOP-PROCESS-SAFETY:START -->
## Desktop and host process safety

The user has experienced desktop-session crashes coinciding with agent-run process tests. In a prior incident, unsafe mock cleanup could expose real process-group signalling; signal attribution remains unproven. Protect the desktop regardless of the task's autonomy or permission mode.

- Never kill or restart the desktop, compositor, display manager, user session manager, user@UID.service, session scopes, or unrelated terminals/agents as cleanup. Never use broad killall, pkill name/pattern matching, kill(-1), or caller-process-group termination. A test failure or stuck subprocess does not authorize host-session cleanup.
- Run tests that exercise process termination, process groups, descendant cleanup, mocked process identities, or signal failure paths only inside a verified separate PID namespace/container/VM that cannot address host processes. Verify isolation before executing tests; no --pid=host or equivalent. A venv, worktree, tmux/herdr pane, new session, or process group alone is NOT PID isolation. If isolation is unavailable, stop execution and use static review or fully intercepted, non-spawning tests; never silently fall back to live-host signalling.
- Do not let fake Popen objects or mocked PIDs reach real os.kill, os.killpg, terminate, or kill. Use explicit fake process identities or a Popen mock that raises on unexpected launches. Install signal guards before registering reader/process cleanup so LIFO cleanup runs before restoring real signals. Keep guards active until all closer threads finish. Cover setup failures, assertion failures, cleanup exceptions and cancellation.
- Production teardown must reject non-integer, boolean, zero, negative, caller-group, reaped and unowned identities. A numeric PID or matching process name alone is not ownership proof. Preserve owned child identity through signalling and reaping; serialize cancellation with teardown. Prefer process handles/pidfds or equivalent ownership-aware mechanisms where suitable.
- Outside isolated tests, stop only an exact process created and still owned by the current task, after verifying identity and scope. Prefer its documented graceful shutdown. Do not escalate to a broader target if shutdown fails.
- Delegating agents must include these constraints in child/pane tasks and verify the execution boundary, not assume a child inherited it. Never delegate around a safety restriction.
- After a suspected signal escape or desktop crash: halt affected tests, preserve evidence, distinguish correlation from proven attribution, repair failure-path cleanup, and require static review plus isolated validation before resuming. Do not reproduce the incident on the live desktop.

These instructions are safeguards, not an OS security boundary. Do not claim universal protection from prompt rules. New tools, alternate configuration roots and already-running sessions may not load this file; use OS-level isolation for enforcement.
<!-- DESKTOP-PROCESS-SAFETY:END -->
