#!/usr/bin/env python3
"""Generate a concise repo map in .agent/repo-map.md (Python 3 standard library only).

Only content between the markers below is modified; manual sections are preserved.
ctags is used for the symbol summary when available, but the script works without it.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

START = "<!-- GENERATED:REPO-MAP:START -->"
END = "<!-- GENERATED:REPO-MAP:END -->"

IGNORED_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".next",
    ".turbo",
    ".cache",
    # Repo-specific runtime data and noise (see .gitignore).
    ".local",
    "video-samples",
    "uploads",
    ".omx",
    ".omx-state-locks",
}

MANIFESTS = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "README.md",
)

DIR_NOTES = {
    "mkw_tracker": "application package (server, store, capture, reports, static assets)",
    "tests": "unittest-style test suite plus optional browser acceptance modules",
    "tools": "setup, replay, benchmark and CI helper scripts",
    "docs": "user-facing documentation",
    ".agent": "durable project memory for coding agents (this harness)",
    "scripts": "harness maintenance scripts (project_memory.py, build_repo_map.py)",
}


def default_root():
    return Path(__file__).resolve().parent.parent


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


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


def walk_files(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = [d for d in sorted(dirnames) if d not in IGNORED_DIRS and not d.endswith(".egg-info")]
        dirnames[:] = keep
        for name in filenames:
            files.append((Path(dirpath) / name).relative_to(root))
    return files


def load_pyproject(root):
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        import tomllib

        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        proj = data.get("project", {})
        return {
            "name": proj.get("name"),
            "version": proj.get("version"),
            "requires_python": proj.get("requires-python"),
            "dependencies": proj.get("dependencies") or [],
            "scripts": proj.get("scripts") or {},
        }
    except Exception:
        return None


def detect_project_type(root, meta):
    found = [m for m in MANIFESTS if (root / m).is_file()]
    parts = []
    if meta is not None:
        name = meta.get("name") or "unnamed"
        version = meta.get("version") or "?"
        py = meta.get("requires_python") or "unknown Python"
        deps = ", ".join(meta["dependencies"]) if meta["dependencies"] else "no required runtime dependencies"
        parts.append(f"Python application `{name}` v{version} (requires {py}); core deps: {deps}.")
    elif found:
        parts.append("Project type inferred from manifests only.")
    else:
        parts.append("No recognized manifest files; project type unclear.")
    if found:
        parts.append(f"Manifests present: {', '.join(found)}.")
    return " ".join(parts)


def find_packages(root):
    pkgs = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in IGNORED_DIRS or entry.name.endswith(".egg-info"):
            continue
        if (entry / "__init__.py").is_file():
            pkgs.append(entry.name)
    return pkgs


def symbol_summary(root):
    ctags = shutil.which("ctags")
    if not ctags:
        return None
    excludes = [f"--exclude={d}" for d in sorted(IGNORED_DIRS)]
    try:
        proc = subprocess.run(
            [ctags, "-R", "--languages=Python,JavaScript"] + excludes + ["-f", "-"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    counts = {}
    total = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2]:
            kind = parts[2].split("/")[0]
            counts[kind] = counts.get(kind, 0) + 1
            total += 1
    return {"total": total, "counts": counts}


def generate(root):
    files = walk_files(root)
    meta = load_pyproject(root)
    pkgs = find_packages(root)

    lines = []
    lines.append(f"_Generated by `scripts/build_repo_map.py` on {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}. Rerun to refresh._")
    lines.append("")
    lines.append("### Project Type")
    lines.append(detect_project_type(root, meta))
    lines.append("")

    lines.append("### Entrypoints")
    if meta and meta.get("scripts"):
        for name, target in sorted(meta["scripts"].items()):
            lines.append(f"- `{name}` console script -> `{target}` (pyproject.toml)")
    for pkg in pkgs:
        if (root / pkg / "__main__.py").is_file():
            lines.append(f"- `python -m {pkg}` module entrypoint (`{pkg}/__main__.py`)")
    if not meta.get("scripts", {}) and not any((root / p / "__main__.py").is_file() for p in pkgs):
        lines.append("- No entrypoints confidently detected.")
    lines.append("")

    lines.append("### Major Directories")
    per_dir = {}
    for rel in files:
        top = rel.parts[0] if len(rel.parts) > 1 else "."
        per_dir[top] = per_dir.get(top, 0) + 1
    if per_dir:
        lines.append("| Directory | Files | Notes |")
        lines.append("|-----------|-------|-------|")
        for name in sorted(per_dir, key=lambda n: (-per_dir[n], n)):
            note = DIR_NOTES.get(name, "")
            lines.append(f"| `{name}/` | {per_dir[name]} | {note} |")
    else:
        lines.append("- No source directories found (repo may be empty).")
    skipped = sorted(d for d in IGNORED_DIRS if (root / d).is_dir())
    if skipped:
        lines.append(f"- Skipped as noise/runtime: {', '.join(skipped)} (plus `__pycache__/`, `*.egg-info/`).")
    lines.append("")

    lines.append("### Key Files")
    key_files = [
        "pyproject.toml",
        "README.md",
        "AGENTS.md",
        "DESIGN.md",
        "MANIFEST.in",
        ".gitlab-ci.yml",
        ".gitignore",
        "kilo.json",
    ]
    for pkg in pkgs:
        key_files.append(f"{pkg}/__main__.py")
    listed = [f"`{f}`" for f in key_files if (root / f).is_file()]
    if listed:
        lines.extend(f"- {item}" for item in listed)
    else:
        lines.append("- None detected.")
    lines.append("")

    lines.append("### Tests")
    tests_dir = root / "tests"
    test_modules = sorted(p.name for p in tests_dir.glob("test_*.py")) if tests_dir.is_dir() else []
    browser = [p.name for p in (tests_dir.glob("browser_*.py") if tests_dir.is_dir() else [])]
    if test_modules:
        lines.append(f"- {len(test_modules)} `test_*.py` modules under `tests/` (unittest discovery).")
        if len(test_modules) <= 12:
            lines.append("  Modules: " + ", ".join(f"`{m}`" for m in test_modules) + ".")
    else:
        lines.append("- No test suite detected.")
    if browser:
        lines.append("- Optional browser acceptance modules: " + ", ".join(f"`{b}`" for b in sorted(browser)) + ".")
    lines.append("")

    lines.append("### Config Files")
    config_files = [f"`{f}`" for f in ("pyproject.toml", ".gitlab-ci.yml", "MANIFEST.in", ".gitignore", "kilo.json") if (root / f).is_file()]
    if config_files:
        lines.extend(f"- {item}" for item in config_files)
    else:
        lines.append("- None detected.")
    lines.append("")

    lines.append("### Symbol Summary")
    summary = symbol_summary(root)
    if summary is None:
        lines.append("- ctags not available (or failed); symbol summary skipped. Use the search commands below instead.")
    else:
        top_kinds = sorted(summary["counts"].items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        detail = ", ".join(f"{kind}: {count}" for kind, count in top_kinds)
        lines.append(f"- {summary['total']} symbols tagged (Python/JavaScript). Top kinds: {detail}.")
    lines.append("")

    lines.append("### Search Commands Useful for This Repo")
    search = [
        'rg "def <function_name>" mkw_tracker',
        'rg "class <ClassName>" mkw_tracker',
        'rg "TODO|FIXME"',
        'git grep "<string>"',
        ".venv/bin/python -m unittest tests.test_store -v  # run one test module",
    ]
    lines.append("```sh")
    lines.extend(search)
    lines.append("```")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=default_root(), help="Repository root (default: parent of scripts/)")
    args = parser.parse_args()
    root = args.root.resolve()

    map_path = root / ".agent" / "repo-map.md"
    if not map_path.is_file():
        fail(f"{map_path} not found; create it with the GENERATED markers first")
    text = map_path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        fail("GENERATED:REPO-MAP:START/END markers missing from .agent/repo-map.md")

    generated = generate(root)
    start_idx = text.index(START)
    end_idx = text.index(END)
    new_text = text[:start_idx] + START + "\n" + generated.rstrip("\n") + "\n" + END + text[end_idx + len(END):]
    atomic_write_text(map_path, new_text)

    n_files = len(walk_files(root))
    print(f"OK: updated generated section of {map_path} ({n_files} files scanned).")


if __name__ == "__main__":
    main()
