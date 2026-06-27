#!/usr/bin/env python3
"""
Clawness — dependency bootstrap (SessionStart, async).

Plugin installation copies files but does not run pip, so this hook ensures
the Python dependencies are present. It is wired as an async SessionStart hook
so it never blocks the session.

Policy:
  - pyyaml is required and installed if missing.
  - model2vec + numpy (semantic retrieval) are installed BY DEFAULT, because
    semantic is the chosen default. Opt out by setting WRIT_NO_SEMANTIC=1.
  - Everything is best-effort: failures are logged, never raised. Until a dep
    is available, retrieval degrades gracefully (lexical/concept, or skipped).
  - A marker prevents re-attempting a failed install every single session.

This script intentionally has no third-party imports of its own.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RETRY_AFTER_SECONDS = 7 * 24 * 3600  # don't re-attempt a failed install for a week


def data_dir() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("WRIT_CACHE_DIR")
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
        with open(data_dir() / "bootstrap.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def importable(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def recently_attempted(marker: Path) -> bool:
    try:
        return marker.exists() and (time.time() - marker.stat().st_mtime) < RETRY_AFTER_SECONDS
    except Exception:
        return False


def pip_install(packages: list[str]) -> bool:
    """Best-effort pip install for the current interpreter. Tries --user, then
    falls back to --break-system-packages. Returns True on success."""
    base = [sys.executable, "-m", "pip", "install", *packages]
    for extra in (["--user"], ["--user", "--break-system-packages"]):
        try:
            r = subprocess.run(
                base + extra,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=540,
            )
            if r.returncode == 0:
                return True
        except Exception as e:
            log(f"pip error for {packages}: {e}")
    return False


def ensure(name: str, packages: list[str]) -> None:
    if importable(name):
        return
    marker = data_dir() / f".attempted-{name}"
    if recently_attempted(marker):
        log(f"{name} missing but recently attempted — skipping this session")
        return
    log(f"installing {name} ({' '.join(packages)})...")
    ok = pip_install(packages)
    try:
        marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception:
        pass
    log(f"{name} install {'succeeded' if ok else 'failed (will fall back gracefully)'}")


def main() -> None:
    # Drain stdin (SessionStart payload) so the pipe closes cleanly.
    try:
        sys.stdin.read()
    except Exception:
        pass

    log("clawness bootstrap: ensuring Python deps are present "
        "(PyYAML required; model2vec+numpy optional, for semantic search)")

    # Required.
    ensure("yaml", ["pyyaml>=6.0"])

    # Semantic embeddings — on by default, opt out with WRIT_NO_SEMANTIC.
    if not os.environ.get("WRIT_NO_SEMANTIC"):
        ensure("model2vec", ["model2vec>=0.3", "numpy>=1.24"])
    else:
        log("WRIT_NO_SEMANTIC set — skipping model2vec (semantic search off)")

    log("clawness bootstrap: done")

    sys.exit(0)


if __name__ == "__main__":
    main()
