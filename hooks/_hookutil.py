"""
Shared plumbing for the SessionStart note hooks.

Five hooks (`git_check`, `memory_init`, `handoff_check`, `stack_detect`,
`changelog_check`) each need the same three things before they can say anything:
UTF-8 stdio, the payload off stdin, and the project root. They had four independent
copies of that logic; the fifth copy is what prompted this module.

Not registered anywhere — it's imported, not run. Hooks execute as scripts, so
`sys.path[0]` is already this directory and a plain `import _hookutil` resolves.

Everything here fails toward None/silence. A note hook that crashes is worse than
one that says nothing, because a nonzero exit surfaces to the user as a broken hook.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The payload (incl. the project cwd) arrives as UTF-8 on stdin; on Windows stdio
# defaults to cp1252 and would mangle a non-ASCII project path on the way in, or
# fail to encode the note on the way out. Pin UTF-8 both ways at import, before any
# hook has a chance to read or print. The isinstance check narrows to the class that
# actually defines reconfigure() — sys.stdin is typed TextIO, which doesn't — and
# skips an already-replaced stream (e.g. pytest capture).
for _stream in (sys.stdin, sys.stdout):
    if isinstance(_stream, io.TextIOWrapper):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# Make `clawness` importable from a hook run as a bare script.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def read_payload() -> dict | None:
    """The hook payload off stdin, or None if it isn't a parseable JSON object.

    None is distinct from {} on purpose: every hook treats unreadable stdin as
    "something is wrong, say nothing", rather than falling back to os.getcwd() and
    reporting on whatever directory the harness happened to launch in.
    """
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def session_cwd(payload: dict) -> Path | None:
    """The session's working directory, or None in a place not worth acting on.

    Returns None for the home directory and the filesystem root: a session opened
    there isn't a project, and a note about "this project" would be noise (or, for
    memory_init, would litter).
    """
    try:
        cwd_path = Path(payload.get("cwd") or os.getcwd()).resolve()
    except Exception:
        return None
    try:
        if cwd_path == Path.home().resolve() or cwd_path.parent == cwd_path:
            return None
    except Exception:
        pass  # can't resolve home — not a reason to go silent
    return cwd_path


def git_root(cwd_path: Path) -> Path | None:
    """The git work-tree root governing `cwd_path`, or None if there isn't one.

    Anchors per-project files at the project top rather than wherever the session
    happened to open. None also means "not a git project", which several hooks
    treat as a reason to stay quiet.
    """
    if not shutil.which("git"):
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return Path(r.stdout.strip()).resolve()
    except Exception:
        return None


def project_root(cwd: str | Path) -> Path | None:
    """`session_cwd` + `git_root` — the git root of a real project, or None."""
    cwd_path = session_cwd({"cwd": str(cwd)})
    return git_root(cwd_path) if cwd_path else None
