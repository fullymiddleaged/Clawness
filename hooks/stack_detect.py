#!/usr/bin/env python3
"""
Clawness — project stack awareness (SessionStart).

Detects the project's language/framework stack from its files and injects a
concise note so Claude starts the session already knowing "this is a Python +
FastAPI project" rather than inferring it. Standing context, complementary to
the per-prompt rule retrieval (which surfaces matching rules as you work).

Reuses the same detection as `clawness init` (one source of truth). Silent when
nothing recognizable is found, in non-project locations (home dir / filesystem
root), or when disabled via CLAW_NO_STACK_NOTE. Fails open on any error.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Stack domains → human labels, in the order we present them (languages, then
# frameworks, then infra). 'general'/'workflows' are always-on, not stack signals.
_LABELS = [
    ("python", "Python"), ("typescript", "TypeScript"), ("go", "Go"),
    ("rust", "Rust"), ("java", "Java"), ("bash", "Bash"),
    ("fastapi", "FastAPI"), ("nextjs", "Next.js"), ("react", "React"),
    ("capacitor", "Capacitor"), ("css", "CSS"),
    ("sql", "SQL"), ("docker", "Docker"),
]


def _project_root(cwd_path: Path) -> Path:
    """The git work-tree root if there is one, else cwd — so we scan the project
    top, not whatever subfolder the session happened to open in."""
    if shutil.which("git"):
        try:
            r = subprocess.run(
                ["git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return Path(r.stdout.strip()).resolve()
        except Exception:
            pass
    return cwd_path


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if os.environ.get("CLAW_NO_STACK_NOTE"):
        sys.exit(0)

    cwd = payload.get("cwd") or os.getcwd()
    try:
        cwd_path = Path(cwd).resolve()
    except Exception:
        sys.exit(0)

    # Don't scan non-project locations (home directory or filesystem root).
    try:
        if cwd_path == Path.home().resolve() or cwd_path.parent == cwd_path:
            sys.exit(0)
    except Exception:
        pass

    try:
        from clawness.init import scan_project
        domains = set(scan_project(_project_root(cwd_path)).get("domains", []))
    except Exception:
        sys.exit(0)

    labels = [label for key, label in _LABELS if key in domains]
    if not labels:
        sys.exit(0)  # nothing recognizable — stay silent

    print(
        "[Clawness] Detected project stack (heuristic from project files): "
        + ", ".join(labels)
        + ". Apply these ecosystems' current conventions and idioms by default, "
        "and prefer their up-to-date best practices. Correct this if the codebase "
        "says otherwise. Silence with CLAW_NO_STACK_NOTE=1."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
