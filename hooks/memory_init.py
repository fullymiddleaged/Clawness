#!/usr/bin/env python3
"""
Clawness — project memory bootstrap (SessionStart).

Two jobs, both about getting `.clawness/` set up correctly in a project:

  1. On the first session, create `.clawness/memory.md` (the per-codebase
     lessons-learned log that the UserPromptSubmit hook injects every turn) seeded
     with a short how-to line, and inject a note so Claude tells the user once that
     the file exists and how to use it ("remember this: ...").

  2. Once, offer to teach git which half of `.clawness/` is shareable. `memory.md`
     and `rules/` are meant to be committed — that is the whole point of putting
     lessons and project rules in the repo. Everything else in there (`handoff.md`,
     `handoffs/`, and the guard/nag/session ledgers) is per-machine state that has
     no business in a diff.

They live in one hook because they are one job: this is the hook that *creates*
`.clawness/`, so it is the one that should say what git ought to do with it. Like
git_check, neither half can prompt the user directly — hooks only inject context,
and Claude relays it.

Notes on the second half, in the order they look wrong:

  * The ignore block is an allowlist, and the trailing `/*` is load-bearing.
    Ignoring the bare directory (`.clawness/`) stops git descending into it at all,
    and the `!` negations below silently do nothing. `.clawness/*` lets git look
    inside, so `!.clawness/memory.md` and `!.clawness/rules/` can pull those back.
    It is an allowlist rather than a list of the volatile files so that a ledger
    added in some future version is ignored by default — the failure direction
    matters, and accidentally committing someone's session state is the bad one.

  * Whether the block is needed is asked of GIT, not the filesystem.
    `git check-ignore` accounts for the global gitignore, nested ones, and a
    wholesale `.clawness/` the user added themselves. If anything already covers
    it, this stays quiet: they have made a decision and it is not ours to revisit.

  * It needs a ledger, unlike the memory-file half. Creating memory.md is
    self-limiting (the file then exists), but a user who says "no thanks" to the
    gitignore offer would be asked again every session forever. `.clawness/gitignore.json`
    records that it was asked, same nag-ledger shape as `changelog.json`. Checked
    LAST, so a session that stays quiet for any other reason doesn't spend the shot.

Gated to real projects: only inside a git work tree, never the home directory or
filesystem root, and never when CLAW_NO_MEMORY is set. Fails open on any error —
memory is a convenience, never a blocker.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import and puts the repo on sys.path.
from _hookutil import git_root, read_payload, session_cwd  # noqa: E402

NOTE = (
    "[Clawness] Created .clawness/memory.md for this project — a lessons-learned log "
    "that Clawness injects into every prompt so hard-won fixes and recurring gotchas "
    "survive across sessions. Early in this session, briefly tell the user it now "
    "exists and that they can grow it by saying \"remember this: ...\" (you append a "
    "terse bullet to the file) or by editing it directly. Suggest committing it so "
    "the team shares the same knowledge. The user can silence this with CLAW_NO_MEMORY=1."
)

# The file used to test coverage. It's the one nobody wants committed and the one
# most likely to already be listed by hand, so it's the fairest single probe.
IGNORE_PROBE = ".clawness/handoff.md"

IGNORE_BLOCK = (
    "# Clawness — per-machine session state (memory.md and rules/ stay shared)\n"
    ".clawness/*\n"
    "!.clawness/memory.md\n"
    "!.clawness/rules/\n"
)

GITIGNORE_NOTE = (
    "[Clawness] This project's .clawness/ directory isn't covered by .gitignore. Part "
    "of it is meant to be committed — memory.md (the shared lessons log) and rules/ "
    "(project rules) — but the rest is per-machine session state: handoff.md, the "
    "archived handoffs/, and the guard, nag and session ledgers. None of that belongs "
    "in a commit or a diff. Early in this session, briefly tell the user and ask "
    "whether to add this block to .gitignore:\n\n"
    + IGNORE_BLOCK
    + "\nThe patterns matter: `.clawness/*` rather than `.clawness/`, because ignoring "
    "the directory itself stops git descending into it and the two exceptions in the "
    "block would silently do nothing. Only edit .gitignore if the user agrees, append the "
    "block rather than rewriting the file, and create .gitignore if it doesn't exist. "
    "If anything under .clawness/ is already tracked, say so — adding ignore rules "
    "does not untrack a committed file, that needs `git rm --cached` on those paths, "
    "which they should do deliberately rather than as part of this. This is asked once "
    "per project. The user can silence it with CLAW_NO_MEMORY=1."
)


def needs_gitignore(root: Path) -> bool:
    """True if no ignore rule currently covers `.clawness/`'s volatile half.

    Asks git rather than reading `.gitignore`, so a global ignore file, a nested
    one, or a wholesale `.clawness/` the user wrote themselves all count as covered.
    Returns False on any failure: an offer we can't justify is worse than silence.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", IGNORE_PROBE],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    # 0 = ignored, 1 = not ignored, anything else = git couldn't answer.
    return r.returncode == 1


def should_ask_gitignore(root: Path) -> bool:
    """True the first time only, recording the ask as it goes.

    An unwritable ledger still returns True — asking twice costs a line of context,
    while never asking costs the feature.
    """
    path = root / ".clawness" / "gitignore.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("asked"):
            return False
    except (OSError, ValueError):
        pass

    try:
        from clawness.plan import atomic_write_text
        atomic_write_text(path, json.dumps({"asked": time.time()}, indent=2) + "\n")
    except Exception:
        pass
    return True


def main() -> None:
    payload = read_payload()
    if payload is None:
        sys.exit(0)

    if os.environ.get("CLAW_NO_MEMORY"):
        sys.exit(0)

    # Don't litter non-project locations (home directory or filesystem root).
    cwd_path = session_cwd(payload)
    if cwd_path is None:
        sys.exit(0)

    # Only auto-create inside a real project. Use the git work-tree root so the
    # file lands at the project root, not wherever the session happened to start.
    root = git_root(cwd_path)
    if root is None:
        sys.exit(0)

    notes: list[str] = []

    # 1. Create the lessons log, once. An existing file means this project is
    #    already set up — no note, but the gitignore half below still runs, since
    #    it has its own separate "have we asked?" state.
    try:
        memory_path = root / ".clawness" / "memory.md"
        if not memory_path.exists():
            from clawness.core import MEMORY_TEMPLATE
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(MEMORY_TEMPLATE, encoding="utf-8")
            notes.append(NOTE)
    except Exception:
        # Couldn't write (permissions, read-only fs, deps not ready) — say nothing
        # about memory, but don't abandon the gitignore offer over it.
        pass

    # 2. Offer the ignore block, once. `should_ask_gitignore` is called LAST and
    #    records as it goes, so a project that's already covered doesn't burn the
    #    one shot and then go silent if the user later removes the rule.
    try:
        if needs_gitignore(root) and should_ask_gitignore(root):
            notes.append(GITIGNORE_NOTE)
    except Exception:
        pass

    for note in notes:
        print(note)
    sys.exit(0)


if __name__ == "__main__":
    main()
