#!/usr/bin/env python3
"""New-coverage-on-upgrade note.

When Clawness gains detection or rules for a stack (Astro landed in 1.18.0), a
project that has always been that stack starts benefiting **automatically** —
retrieval runs against the whole corpus every prompt and the stack filter simply
stops penalising the newly-detected domain. Nothing needs installing or
re-running. What the user does NOT get automatically is any *awareness* that it
happened, so a capability they were waiting for can sit there unnoticed.

That awareness is all this module provides. Three deliberate choices:

* **Gated on the Clawness version changing.** The common path is a string
  compare against a ledger — no corpus load, no extra scan. `stack_detect` has
  already called `scan_project`, so on the rare upgrade turn this costs a
  directory listing of `rules/` and a set difference.

* **Silent on a project's FIRST session.** With no ledger every domain is
  "new", which is noise on a first run and exactly the failure `claude_md_check`
  documents for first-session gating. The first run records and says nothing.

* **The note reports what now MATCHES, and does not claim to know why.** The
  ledger stores `detected ∩ available`, so a difference means "these rules apply
  now and didn't at the last check" — which is true whether the corpus grew or
  the project did. Distinguishing those would mean storing the old corpus's
  domain list, and the wording would then be a claim the data can't support.
  It is also genuinely useful in both directions, so the neutral phrasing costs
  nothing.

Like every other SessionStart note here, it **orients and commissions nothing**
(see the staleness and coverage notes for the two occasions a work-order-shaped
note ate somebody's session). Opt out with CLAW_NO_UPGRADE_NOTE. Fails silent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

LEDGER_NAME = "version.json"

# Domains every project gets regardless of stack. Newly matching one of these is
# not news, and `coverage.has_coverage` treats them the same way.
_ALWAYS_ON = frozenset({"general", "workflows"})


def available_domains(rules_dir: Path) -> set[str]:
    """Domain names the shipped corpus has rules for.

    A directory listing rather than a corpus load: this runs at SessionStart on
    the upgrade turn, and loading/parsing every rule to learn a set of folder
    names would be strictly more work for the same answer. `_mandatory` is
    excluded — those rules bypass retrieval and the stack filter entirely, so
    they are never "newly matched". Never raises.
    """
    try:
        return {
            p.name for p in rules_dir.iterdir()
            if p.is_dir() and not p.name.startswith(("_", "."))
        }
    except OSError:
        return set()


def newly_covered(detected, rules_dir: Path, stored: list[str]) -> list[str]:
    """Domains that match this project now and did not at the last check."""
    now = (set(detected) & available_domains(rules_dir)) - _ALWAYS_ON
    return sorted(now - set(stored))


def check_upgrade(root: Path, version: str, detected, rules_dir: Path) -> list[str]:
    """Newly-matching domains to report, recording the ledger as it goes.

    Returns [] — and writes nothing — when the version is unchanged, so the
    steady-state cost is one small JSON read. Returns [] but DOES record on a
    project's first session, so the next upgrade has a baseline to diff against.

    An unreadable ledger is treated as a first session: it records and stays
    quiet. That is the opposite of `coverage.unasked`, which warns twice rather
    than never — the asymmetry is deliberate. A missed *uncovered-stack* warning
    means the user never learns Clawness is doing little for them; a missed
    *new-coverage* note means they miss an announcement about rules that are
    already working. Silence is the cheaper failure here, and a spurious "new
    coverage" note on a corrupt ledger would be the kind of false alarm that
    teaches people to ignore the real one.
    """
    path = root / ".clawness" / LEDGER_NAME
    prior_version = ""
    stored: list[str] = []
    first_session = True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            prior_version = str(data.get("version") or "")
            if isinstance(data.get("covered"), list):
                stored = [x for x in data["covered"] if isinstance(x, str)]
            first_session = not prior_version
    except (OSError, ValueError):
        pass

    if prior_version == version:
        return []

    fresh = [] if first_session else newly_covered(detected, rules_dir, stored)
    covered = sorted((set(detected) & available_domains(rules_dir)) - _ALWAYS_ON)
    try:
        from .plan import atomic_write_text
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps(
                {"version": version, "covered": covered, "updated": time.time()},
                indent=2,
            ) + "\n",
        )
    except Exception:
        pass
    return fresh


# Text is a tested artifact, like the staleness and coverage notes. It states a
# fact and stops: the rules are ALREADY live, so there is nothing for Claude to
# run, install, or author, and saying otherwise is how a note becomes the task.
_NOTE = (
    "[Clawness] Clawness updated to {version}, and this project now matches rule "
    "coverage it didn't before: {labels}. Those rules are already active this "
    "session — retrieval picks them up automatically, so nothing needs running or "
    "installing. Mention this to the user in a sentence; don't start any work on "
    "it. Silence with CLAW_NO_UPGRADE_NOTE=1."
)


def render_note(labels: list[str], version: str) -> str:
    """The note, or "" when there is nothing newly covered."""
    if not labels:
        return ""
    return _NOTE.format(version=version, labels=", ".join(labels))
