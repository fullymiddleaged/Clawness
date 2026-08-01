#!/usr/bin/env python3
"""
Clawness — changelog check (SessionStart).

A changelog reconstructed from `git log` at release time records commits, not
changes; the entry has to be written while the change is still fresh. This hook
makes that visible once per session.

Two cases, and they are deliberately asymmetric:

  CHANGELOG present  — a short reminder every session. Cheap, and it's the case
                       where acting on it is a one-line edit.
  CHANGELOG absent   — asked exactly ONCE per project, ever, via a ledger in
                       `.clawness/changelog.json`. Not every repo wants one, and a
                       question re-asked every session is a question nobody answers.
                       Like git_check, the hook never creates the file itself: it
                       tells Claude to ask, and to act only on agreement.

Gated to git work trees (a scratch directory has no use for a changelog), never the
home directory or filesystem root. Opt out with CLAW_NO_CHANGELOG_CHECK=1, or drop a
`.clawness/changelog-check-off` marker in a project that should never be asked.
Fails open on every path.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import and puts the repo on sys.path.
from _hookutil import git_root, read_payload, session_cwd  # noqa: E402

# Where a changelog conventionally lives. First hit wins; the name is reported back
# so the note names the file the user actually has.
CANDIDATES = [
    "CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt", "CHANGELOG",
    "docs/CHANGELOG.md", "changelog.md",
]

PRESENT_NOTE = (
    "[Clawness] This project keeps a changelog ({name}). When you make a "
    "user-visible change in this session — a feature, a fix, a breaking change, a "
    "removed option — add its line to the Unreleased section as part of the same "
    "work, not at release time. Write what changed for someone using the project, "
    "not the commit subject. Skip it for changes nobody outside the repo can "
    "observe: refactors, test-only edits, formatting. Silence with "
    "CLAW_NO_CHANGELOG_CHECK=1."
)

ABSENT_NOTE = (
    "[Clawness] This project has no changelog. If the work in this session turns out "
    "to be user-visible, mention once that a CHANGELOG.md would capture it and ask "
    "whether the user wants one — then create it (Keep a Changelog format, starting "
    "with an Unreleased section) only if they say yes. Never add one uninvited: "
    "plenty of projects deliberately don't have one. This is asked once per project "
    "and won't come back. Silence with CLAW_NO_CHANGELOG_CHECK=1."
)


def find_changelog(root: Path) -> str:
    """Name of the project's changelog file, or "" if there isn't one."""
    for rel in CANDIDATES:
        try:
            if (root / rel).is_file():
                return rel
        except OSError:
            continue
    return ""


def should_ask(root: Path) -> bool:
    """True the first time a project is asked about a missing changelog; records it.

    Same shape as `model_advisor.should_advise` — a per-project ledger so the
    question is asked once and never again. An unwritable ledger still returns True:
    asking once is the point, and a repeat is a far smaller cost than never asking.
    """
    path = root / ".clawness" / "changelog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("asked"):
            return False
    except (OSError, ValueError):
        pass

    try:
        from clawness.plan import atomic_write_text
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"asked": time.time()}, indent=2) + "\n")
    except Exception:
        pass
    return True


def main() -> None:
    payload = read_payload()
    if payload is None:
        sys.exit(0)

    if os.environ.get("CLAW_NO_CHANGELOG_CHECK"):
        sys.exit(0)

    cwd_path = session_cwd(payload)
    if cwd_path is None:
        sys.exit(0)

    # A changelog belongs to a versioned project. No git work tree, nothing to say.
    root = git_root(cwd_path)
    if root is None:
        sys.exit(0)

    if (root / ".clawness" / "changelog-check-off").exists():
        sys.exit(0)

    name = find_changelog(root)
    if name:
        print(PRESENT_NOTE.format(name=name))
        sys.exit(0)

    # Absent: ask once, ever. `should_ask` is called LAST so a session that would
    # have stayed quiet for any reason above doesn't burn the one shot.
    if should_ask(root):
        print(ABSENT_NOTE)
    sys.exit(0)


if __name__ == "__main__":
    main()
