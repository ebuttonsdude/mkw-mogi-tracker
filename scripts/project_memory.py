#!/usr/bin/env python3
"""Manage durable project memory under .agent/ using only the Python 3 standard library.

Commands:
    status                 Show current state and task table.
    validate               Check state.json and tasks.md consistency; exit nonzero on failure.
    new-task --id T-NNN ... Create a task row in tasks.md (default status: backlog).
    set-current T-NNN      Set the active task (must exist in tasks.md).
    start T-NNN            Mark a task in_progress and make it current.
    block T-NNN --reason   Mark a task blocked; record reason in state notes.
    complete T-NNN         Mark a task done; clear current_task_id if it was current.
    note --text "..."      Append a note to state.json.

State is written atomically (temp file + os.replace). Task IDs must be unique T-NNN.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VALID_STATUSES = ("backlog", "ready", "in_progress", "blocked", "done")
TASK_ID_RE = re.compile(r"^T-\d{3}$")
STATE_FIELDS = (
    "schema_version",
    "project",
    "updated_at",
    "branch",
    "current_task_id",
    "goal",
    "status",
    "key_files",
    "commands",
    "next_actions",
    "risks",
    "notes",
)
STATUS_ARRAYS = ("done", "in_progress", "blocked")
COMMAND_FIELDS = ("install", "test", "lint", "typecheck", "build")


def default_root():
    return Path(__file__).resolve().parent.parent


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fail(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def atomic_write_text(path, text):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def state_path(root):
    return root / ".agent" / "state.json"


def tasks_file(root):
    return root / ".agent" / "tasks.md"


def load_state(root):
    path = state_path(root)
    if not path.is_file():
        fail(f"{path} not found")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")


def save_state(root, state):
    state["updated_at"] = now_iso()
    atomic_write_text(state_path(root), json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def parse_tasks(root):
    """Return (lines, tasks). Each task: {id, title, status, line, cells}."""
    path = tasks_file(root)
    if not path.is_file():
        fail(f"{path} not found")
    lines = path.read_text(encoding="utf-8").splitlines()
    tasks = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [c.strip() for c in stripped[1:-1].split("|")]
        if len(cells) >= 3 and TASK_ID_RE.match(cells[0]):
            tasks.append(
                {"id": cells[0], "title": cells[1], "status": cells[2], "line": idx, "cells": cells}
            )
    return lines, tasks


def sanitize(cell):
    return cell.replace("|", "/").replace("\n", " ").strip()


def require_task(root, task_id):
    _, tasks = parse_tasks(root)
    if not any(t["id"] == task_id for t in tasks):
        fail(f"task {task_id} not found in .agent/tasks.md")


def set_task_status(root, task_id, status):
    if status not in VALID_STATUSES:
        fail(f"invalid status {status!r}; valid statuses: {', '.join(VALID_STATUSES)}")
    lines, tasks = parse_tasks(root)
    match = [t for t in tasks if t["id"] == task_id]
    if not match:
        fail(f"task {task_id} not found in .agent/tasks.md")
    cells = list(match[0]["cells"])
    while len(cells) < 6:
        cells.append("")
    cells[2] = status
    lines[match[0]["line"]] = "| " + " | ".join(cells) + " |"
    atomic_write_text(tasks_file(root), "\n".join(lines) + "\n")


def add_task(root, task_id, title, criteria, files, verify, status):
    if not TASK_ID_RE.match(task_id):
        fail(f"task id must match T-NNN (e.g. T-002), got {task_id!r}")
    lines, tasks = parse_tasks(root)
    if any(t["id"] == task_id for t in tasks):
        fail(f"task {task_id} already exists in .agent/tasks.md")
    row = "| " + " | ".join(
        [task_id, sanitize(title), status, sanitize(criteria), sanitize(files), sanitize(verify)]
    ) + " |"
    last_row = max((t["line"] for t in tasks), default=None)
    if last_row is None:
        lines.append(row)
    else:
        lines.insert(last_row + 1, row)
    atomic_write_text(tasks_file(root), "\n".join(lines) + "\n")


def sync_status_arrays(state, task_id, status):
    for arr in STATUS_ARRAYS:
        if task_id in state["status"][arr]:
            state["status"][arr].remove(task_id)
    if status in STATUS_ARRAYS:
        state["status"][status].append(task_id)


def cmd_status(root, _args):
    state = load_state(root)
    _, tasks = parse_tasks(root)
    print(f"project : {state.get('project')}")
    print(f"branch  : {state.get('branch') or '(unknown)'}")
    print(f"updated : {state.get('updated_at')}")
    current = state.get("current_task_id")
    print(f"current : {current if current else '(none)'}")
    print(f"goal    : {state.get('goal')}")
    for arr in STATUS_ARRAYS:
        ids = ", ".join(state["status"].get(arr, [])) or "-"
        print(f"{arr:<12}: {ids}")
    next_actions = state.get("next_actions", [])
    if next_actions:
        print("next actions:")
        for item in next_actions:
            print(f"  - {item}")
    print("tasks:")
    for t in tasks:
        print(f"  {t['id']} [{t['status']}] {t['title']}")


def cmd_validate(root, _args):
    errors = []
    path = state_path(root)
    state = None
    if not path.is_file():
        errors.append(f"{path} is missing")
    else:
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except json.JSONDecodeError as exc:
            errors.append(f"state.json is not valid JSON: {exc}")

    if isinstance(state, dict):
        for field in STATE_FIELDS:
            if field not in state:
                errors.append(f"state.json missing required field: {field}")
        updated_at = state.get("updated_at")
        if "updated_at" in state:
            if not isinstance(updated_at, str) or not updated_at:
                errors.append("state.json updated_at must be a non-empty string")
            else:
                try:
                    datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                except ValueError:
                    errors.append(f"state.json updated_at is not ISO 8601: {updated_at!r}")
        status = state.get("status")
        if isinstance(status, dict):
            for arr in STATUS_ARRAYS:
                if not isinstance(status.get(arr), list):
                    errors.append(f"state.json status.{arr} must be a list of task IDs")
        else:
            errors.append("state.json status must be an object with done/in_progress/blocked lists")
        commands = state.get("commands")
        if isinstance(commands, dict):
            for field in COMMAND_FIELDS:
                if not isinstance(commands.get(field), str):
                    errors.append(f"state.json commands.{field} must be a string (empty allowed)")
        else:
            errors.append(
                "state.json commands must be an object with install/test/lint/typecheck/build strings"
            )

    tpath = tasks_file(root)
    task_ids = []
    if not tpath.is_file():
        errors.append(f"{tpath} is missing")
    else:
        _, tasks = parse_tasks(root)
        seen = set()
        for t in tasks:
            if t["id"] in seen:
                errors.append(f"duplicate task id {t['id']} in tasks.md")
            seen.add(t["id"])
            if t["status"] not in VALID_STATUSES:
                errors.append(f"task {t['id']} has invalid status {t['status']!r}")
        task_ids = sorted(seen)

    if isinstance(state, dict):
        current = state.get("current_task_id")
        if current is not None and current not in task_ids:
            errors.append(f"current_task_id {current!r} does not match a task in tasks.md")
        status = state.get("status")
        if isinstance(status, dict) and task_ids:
            for arr in STATUS_ARRAYS:
                for tid in status.get(arr, []):
                    if tid not in task_ids:
                        errors.append(f"state.json status.{arr} contains unknown task {tid!r}")

    if errors:
        print("VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    print("OK: .agent/state.json and .agent/tasks.md validate.")


def cmd_new_task(root, args):
    status = args.status or "backlog"
    if status not in VALID_STATUSES:
        fail(f"invalid status {status!r}; valid statuses: {', '.join(VALID_STATUSES)}")
    add_task(root, args.id, args.title, args.criteria, args.files, args.verify, status)
    state = load_state(root)
    sync_status_arrays(state, args.id, status)
    save_state(root, state)
    print(f"Added task {args.id} [{status}] to .agent/tasks.md.")


def cmd_set_current(root, args):
    require_task(root, args.task_id)
    state = load_state(root)
    state["current_task_id"] = args.task_id
    save_state(root, state)
    print(f"Current task set to {args.task_id}.")


def cmd_start(root, args):
    require_task(root, args.task_id)
    set_task_status(root, args.task_id, "in_progress")
    state = load_state(root)
    sync_status_arrays(state, args.task_id, "in_progress")
    state["current_task_id"] = args.task_id
    save_state(root, state)
    print(f"Task {args.task_id} started (status in_progress, now current).")


def cmd_block(root, args):
    require_task(root, args.task_id)
    set_task_status(root, args.task_id, "blocked")
    state = load_state(root)
    sync_status_arrays(state, args.task_id, "blocked")
    note = f"[{args.task_id}] blocked: {args.reason}"
    notes = state.setdefault("notes", [])
    if note not in notes:
        notes.append(note)
    save_state(root, state)
    print(f"Task {args.task_id} marked blocked; reason recorded in state notes.")


def cmd_complete(root, args):
    require_task(root, args.task_id)
    set_task_status(root, args.task_id, "done")
    state = load_state(root)
    sync_status_arrays(state, args.task_id, "done")
    if state.get("current_task_id") == args.task_id:
        state["current_task_id"] = None
    save_state(root, state)
    print(f"Task {args.task_id} marked done.")


def cmd_note(root, args):
    state = load_state(root)
    notes = state.setdefault("notes", [])
    notes.append(args.text)
    save_state(root, state)
    print("Note appended to .agent/state.json.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=default_root(), help="Repository root (default: parent of scripts/)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show current state and task table").set_defaults(func=cmd_status)
    sub.add_parser("validate", help="Validate state.json and tasks.md").set_defaults(func=cmd_validate)

    p_new = sub.add_parser("new-task", help="Create a new task")
    p_new.add_argument("--id", required=True, help="Task ID in T-NNN form")
    p_new.add_argument("--title", required=True)
    p_new.add_argument("--criteria", default="", help="Acceptance criteria")
    p_new.add_argument("--files", default="", help="Comma-separated main files")
    p_new.add_argument("--verify", default="", help="Verification command")
    p_new.add_argument("--status", default=None, help=f"Initial status (default backlog); one of {', '.join(VALID_STATUSES)}")
    p_new.set_defaults(func=cmd_new_task)

    p_cur = sub.add_parser("set-current", help="Set the active task ID")
    p_cur.add_argument("task_id")
    p_cur.set_defaults(func=cmd_set_current)

    p_start = sub.add_parser("start", help="Mark a task in_progress and make it current")
    p_start.add_argument("task_id")
    p_start.set_defaults(func=cmd_start)

    p_block = sub.add_parser("block", help="Mark a task blocked")
    p_block.add_argument("task_id")
    p_block.add_argument("--reason", required=True)
    p_block.set_defaults(func=cmd_block)

    p_done = sub.add_parser("complete", help="Mark a task done")
    p_done.add_argument("task_id")
    p_done.set_defaults(func=cmd_complete)

    p_note = sub.add_parser("note", help="Append a note to state.json")
    p_note.add_argument("--text", required=True)
    p_note.set_defaults(func=cmd_note)

    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / ".agent").is_dir():
        fail(f"{root} does not contain a .agent/ directory")
    args.func(root, args)


if __name__ == "__main__":
    main()
