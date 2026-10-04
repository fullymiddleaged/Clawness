"""
SAST readiness: which scanners are installed, and whether to offer one.

`/clawness:security-audit` runs any real scanner it finds and folds the SARIF into
the findings ledger; with none installed it falls back to `clawness scan`, a
heuristic tripwire. This module is what lets the hook say which of those the user
is about to get, on the turn they ask for a security scan, so a missing scanner is
offered at the moment it matters instead of discovered in the report.

Three choices, all about keeping it easy:

* **One install, not a menu.** Semgrep covers most languages with real taint
  analysis and installs with one command, so the offer names only that. The skill
  still runs Bandit/Gitleaks/Trivy/CodeQL when they're present.
* **Once per project** (`.clawness/sast.json`). A declined offer stays declined;
  asking on every security prompt is the nag that gets the whole note ignored.
  Detection itself stays live, so a scanner installed later is picked up silently.
* **The fallback is always named.** Declining never blocks the audit — the skill
  runs on Clawness's own scan, and the note says so.

`which` is only called on prompts that already look like a security request, so
the per-prompt cost for every other prompt is zero. Fails silent on every path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

# Order is the order they're reported in. All emit SARIF the ledger can ingest.
SCANNERS = ("semgrep", "codeql", "bandit", "gitleaks", "trivy")

INSTALL_HINT = (
    "`uv tool install semgrep` or `pipx install semgrep` "
    "(macOS also `brew install semgrep`; Windows support is beta)"
)

_LEDGER = "sast.json"


def installed_scanners(which: "Callable[[str], str | None] | None" = None) -> list[str]:
    """Names from SCANNERS found on PATH, in SCANNERS order."""
    which = which or shutil.which
    found = []
    for name in SCANNERS:
        try:
            if which(name):
                found.append(name)
        except Exception:
            continue
    return found


def _is_project(root: Path) -> bool:
    return (root / ".git").exists() or (root / ".clawness").is_dir()


def should_offer(root: Path) -> bool:
    """True the first time for this project, recording that it was offered.

    Outside a project (no .git or .clawness) there is nowhere sensible to keep a
    ledger, so it offers without recording rather than writing .clawness/ into an
    arbitrary directory. Call it LAST, so a turn that wouldn't offer anyway never
    burns the project's one shot."""
    try:
        if not _is_project(root):
            return True
        ledger = root / ".clawness" / _LEDGER
        if ledger.is_file():
            try:
                if json.loads(ledger.read_text(encoding="utf-8")).get("offered"):
                    return False
            except (ValueError, AttributeError):
                pass  # corrupt: offer again rather than never
        from clawness.plan import atomic_write_text
        atomic_write_text(ledger, json.dumps({"offered": True}) + "\n")
        return True
    except Exception:
        return False


def render_line(installed: list[str], offer: bool) -> str:
    """The suggested-action line for a security-scan prompt."""
    if installed:
        return (
            f"SAST scanners installed: {', '.join(installed)}. /clawness:security-audit "
            "runs them and folds their findings into the ledger alongside its own scan."
        )
    if offer:
        return (
            "No SAST scanner is installed, so /clawness:security-audit would rely on "
            "Clawness's own scan, a heuristic tripwire without cross-file taint "
            f"analysis. Before starting, offer the user one install: {INSTALL_HINT}. "
            "Check its current release first. If they decline, run the audit anyway "
            "on the built-in scan. Offer this once, without repeating it."
        )
    return (
        "No SAST scanner is installed (already offered for this project); "
        "/clawness:security-audit runs on Clawness's own scan."
    )


def sast_line(cwd: str, which: "Callable[[str], str | None] | None" = None) -> str:
    """Line for the hook, or "" on any failure."""
    try:
        installed = installed_scanners(which)
        if installed:
            return render_line(installed, offer=False)
        from clawness.plan import find_project_root
        return render_line([], offer=should_offer(find_project_root(Path(cwd))))
    except Exception:
        return ""
