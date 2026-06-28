#!/usr/bin/env python3
"""
Clawness — dependency bootstrap (SessionStart, async).

Plugin installation copies files but does not run pip, so this hook ensures
the Python dependencies are present. It is wired as an async SessionStart hook
so it never blocks the session.

Policy:
  - pyyaml is the only dependency — it's all the pure-Python lexical + concept
    retrieval needs. Installed if missing.
  - Everything is best-effort: failures are logged, never raised. Until pyyaml
    is available the rule hook degrades gracefully (injects nothing rather than
    erroring the prompt).
  - A failed install is simply retried on the next session — no lockout — so a
    fixed network/permission issue is picked up immediately.
  - Every step (interpreter, each pip attempt + result, version, final status)
    is written to bootstrap.log in the plugin data dir — the install record,
    since Claude Code can't surface hook output to the user. `claude --debug`
    shows it live.

This script intentionally has no third-party imports of its own.
"""

import os
import subprocess
import sys
import time
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("CLAW_CACHE_DIR")
    if not base:
        base = str(Path.home() / ".cache" / "clawness")
    p = Path(base)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def log(msg: str) -> None:
    try:
        with open(data_dir() / "bootstrap.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def importable(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def _module_version(name: str) -> str:
    try:
        return getattr(__import__(name), "__version__", "unknown")
    except Exception:
        return "unknown"


def pip_install(packages: list[str]) -> bool:
    """Best-effort pip install for the current interpreter. Tries a plain
    install first (works in a venv/conda and in user-writable Pythons), then
    --user (avoids needing admin on a system-wide Python; pip rejects --user
    inside a venv, so plain must come first), then adds --break-system-packages
    (Debian / PEP 668). Logs each attempt + result. Returns True on success."""
    base = [sys.executable, "-m", "pip", "install", *packages]
    for extra in ([], ["--user"], ["--user", "--break-system-packages"]):
        log("  trying: pip install " + " ".join([*packages, *extra]))
        try:
            r = subprocess.run(
                base + extra,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=540,
                text=True,
            )
            if r.returncode == 0:
                log("  ok")
                return True
            tail = (r.stdout or "").strip().splitlines()
            log(f"  exit {r.returncode}: {tail[-1] if tail else '(no output)'}")
        except Exception as e:
            log(f"  error: {e}")
    return False


def ensure(name: str, packages: list[str]) -> bool:
    """Install *packages* if *name* isn't importable. Returns True if it's
    available afterward. Best-effort and simply retried next session on
    failure — pip fails fast when offline and caches downloads, so re-attempting
    is cheap."""
    if importable(name):
        log(f"{name}: already present (v{_module_version(name)})")
        return True
    log(f"{name}: missing — installing {' '.join(packages)}")
    if pip_install(packages) and importable(name):
        log(f"{name}: installed (v{_module_version(name)})")
        return True
    log(f"{name}: NOT available — will retry next session (rules won't inject until then)")
    return False


def main() -> None:
    # Drain stdin (SessionStart payload) so the pipe closes cleanly.
    try:
        sys.stdin.read()
    except Exception:
        pass

    log("=== clawness bootstrap ===")
    log(f"python : {sys.executable}")
    log(f"version: {sys.version.split()[0]}")

    # PyYAML is the only dependency — retrieval is pure-Python lexical + concept.
    ok = ensure("yaml", ["pyyaml>=6.0"])

    log(
        "bootstrap "
        + ("ready — rule injection active" if ok
           else "incomplete — PyYAML unavailable; rules activate once it installs")
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
