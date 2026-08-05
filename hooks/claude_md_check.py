#!/usr/bin/env python3
"""
Clawness — CLAUDE.md size check (SessionStart).

CLAUDE.md is loaded by the harness, in full, on every turn, before any hook runs.
That makes it the one context cost nothing in this plugin can cap: the rules block
is budgeted, the lessons log is ranked and budgeted, CLAUDE.md is neither. A file
that grew a paragraph at a time is invisible until someone measures it — this repo's
own is ~9,151 tokens a turn, about ten times its always-on rules block.

This hook measures it and, once, says so. It never edits the file and never
reorganises it: the note reports the number and suggests the USER do a revision pass
when it suits them — the same consent shape as `git_check`'s `git init` and
`changelog_check`'s offer to create a changelog.

Design notes, in the order people try to "simplify" them:

  * This hook is the DIAGNOSIS. It does not offer to reorganise the file and must
    not regain that. 1.7.0 shipped a guided three-way relocation (long rationale to
    `.clawness/rules/`, one-line traps to memory.md, "don't undo this" staying put).
    Dogfooding it on this repo worked, and that was the problem: it consumed most of
    a session opened for something else. The bug was never the content — it was that
    a SessionStart note fires *before the user has said what they came for*, so it
    cannot propose a long destructive refactor. Diagnosis is cheap and welcome; the
    remedy needs consent the note is in no position to ask for.

  * The REMEDY lives behind an explicit invocation, in two places. Claude Code's own
    `/doctor` proposes CLAUDE.md trims natively (v2.1.206+) and migrates what remains
    into skills and nested CLAUDE.md files. `skills/claude-md/SKILL.md` is this
    plugin's counterpart, and exists only for the two destinations `/doctor` can't
    know about — `.clawness/rules/` and `.clawness/memory.md`. That skill carries the
    1.7.0 content, unchanged, with the timing fixed: the user typing the slash command
    IS the consent. The note names both and starts neither. If a future version wants
    the hook to do more, the answer is to improve the skill.

  * What the note recommends is a TRIM. Cut what the codebase already makes obvious —
    directory layouts, dependency lists, architecture summaries — and keep pitfalls,
    rationale, and conventions that differ from tool defaults. Anything shaped like
    "don't undo this without reading why" is the last thing to touch: it is
    load-bearing exactly when the prompt gives no hint that it applies, so a missed
    rule is a slightly worse answer while a missed "don't undo this" is the
    regression it existed to prevent.

  * Tokens are ESTIMATED at chars/4, never tokenized. PyYAML is this plugin's only
    dependency and a nudge is not a reason to change that. The note says "roughly"
    on purpose — a guess stated as a fact is a number people argue with instead of
    acting on.

  * `@path` imports are still not followed, and the reason is now a measured one
    rather than caution. The harness loads imports EAGERLY — "imported files are
    expanded and loaded into context at launch", recursive to four hops — so a
    CLAUDE.md reorganised into `@` references costs exactly what it did before, and
    our number under-reports it by the size of the imported tree. That is a real
    false negative, not a hypothetical one, but it errs toward silence, which is the
    right direction for a nudge. Following them means reimplementing the harness's
    parser (relative to the containing file, skipping code spans and fenced blocks);
    until that is worth doing, the note stays conservative and says so.

  * The remedy the note points at is a TRIM, then path-scoped rules — never imports.
    `.claude/rules/*.md` with `paths:` frontmatter loads only when Claude reads a
    matching file, and a nested `CLAUDE.md` loads when Claude reads that directory.
    Those are the only two mechanisms that actually reduce per-turn cost; `@path`
    imports and this plugin's own `.clawness/rules/` do not (the latter is retrieved
    and budgeted, which is cheaper, but it is still this hook proposing a refactor —
    see above). `/doctor` already performs both halves.

  * Firing this on the FIRST session in a project instead was considered and
    rejected. It sounds tidier — catch it during setup, when the user is already in
    a configuring frame of mind — but it inverts the timing. On a project's first
    session CLAUDE.md is usually absent or small; the bloat this check exists for
    accrues a paragraph at a time over months. Gating to the first session would
    mean never firing for the only case that matters. The trigger has to stay the
    size, whenever the size happens.

  * The ledger stores the SIZE, not a boolean. changelog_check asks a yes/no question
    once and is done; this one is a magnitude, and "asked at 6k, silent forever at
    30k" is precisely the failure mode this project already learned the hard way with
    the plan gate — an absent prompt is indistinguishable from a working one. So it
    re-arms once the file has grown by half again.

Gated to git work trees, never the home directory or filesystem root. Opt out with
CLAW_NO_CLAUDE_MD_CHECK=1, or drop a `.clawness/claude-md-check-off` marker in a
project that should never be asked. Fails open (silent) on every path.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import and puts the repo on sys.path.
from _hookutil import git_root, read_payload, session_cwd  # noqa: E402

# The files the harness loads as project instructions. All of them are paid for on
# every turn, so all of them count toward the total.
CANDIDATES = ["CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"]

# Above this many estimated tokens, CLAUDE.md costs more per turn than Clawness's
# entire injection at full stretch (~851 mandatory + ~3,149 ranked), and pays it on
# every turn of every session forever. Below it, a project-instructions file is just
# doing its job — this hook says nothing, in line with the guard's rule about never
# nagging normal work.
DEFAULT_LIMIT_TOKENS = 6000

# Re-ask once the file is half again as big as it was when we last asked.
REARM_GROWTH = 1.5

# Rough tokens-per-character for English prose. Deliberately crude; see the docstring.
CHARS_PER_TOKEN = 4

NOTE = (
    "[Clawness] This project's CLAUDE.md is roughly {tokens:,} tokens ({chars:,} "
    "characters across {files}). It is loaded in full before any hook runs and stays "
    "in context for every turn of the session — for comparison, Clawness's own rules "
    "block tops out around 4,000 tokens and is budgeted. Mention this to the user in "
    "two or three lines: the size, that it is a cost paid on every turn of every "
    "session, and that a file this large also makes Claude follow any single "
    "instruction in it less reliably. Then tell them a revision pass is worth doing "
    "when it suits them, and name the two tools that do it: Claude Code's own "
    "'/doctor', which proposes trims natively, and '/clawness:claude-md', which does "
    "the same and also places what survives into this project's Clawness rules. "
    "Do NOT start that work now, do not reorganise the file yourself, and never edit "
    "CLAUDE.md uninvited: plenty of projects want it exactly as it is, restructuring "
    "it is the user's call, and it is not a job to begin in the middle of a session "
    "they opened for something else. If they ask you to do it now, run "
    "'/clawness:claude-md' rather than improvising. This is asked once per project, "
    "and again only if the file grows by half. Silence with CLAW_NO_CLAUDE_MD_CHECK=1."
)


def measure(root: Path) -> tuple[int, list[str]]:
    """Total characters of project-instruction files, and the ones that exist.

    Unreadable files are skipped rather than guessed at: a size we can't read is not
    a size worth reporting, and this hook's whole value is that its number is real.
    """
    total = 0
    found: list[str] = []
    for rel in CANDIDATES:
        path = root / rel
        try:
            if not path.is_file():
                continue
            # errors="replace" because only the length matters here, and a stray
            # byte in someone's CLAUDE.md is not a reason to go silent.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(text)
        found.append(rel)
    return total, found


def limit_tokens() -> int:
    """Threshold in estimated tokens, from CLAW_CLAUDE_MD_LIMIT or the default."""
    raw = os.environ.get("CLAW_CLAUDE_MD_LIMIT", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LIMIT_TOKENS
    return value if value > 0 else DEFAULT_LIMIT_TOKENS


def should_ask(root: Path, tokens: int) -> bool:
    """True if this project hasn't been told, or has grown by half since it was.

    Records the size it asked at, so the next decision is a comparison rather than a
    boolean. An unwritable ledger still returns True: asking twice is a far smaller
    cost than never asking again.
    """
    path = root / ".clawness" / "claude_md.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("asked"):
            previous = data.get("tokens")
            if isinstance(previous, (int, float)) and previous > 0:
                if tokens < previous * REARM_GROWTH:
                    return False
            else:
                # Asked before, but we can't tell at what size — treat it as answered
                # rather than re-asking on every session from here on.
                return False
    except (OSError, ValueError):
        pass

    try:
        from clawness.plan import atomic_write_text
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path, json.dumps({"asked": time.time(), "tokens": tokens}, indent=2) + "\n"
        )
    except Exception:
        pass
    return True


def main() -> None:
    payload = read_payload()
    if payload is None:
        sys.exit(0)

    if os.environ.get("CLAW_NO_CLAUDE_MD_CHECK"):
        sys.exit(0)

    cwd_path = session_cwd(payload)
    if cwd_path is None:
        sys.exit(0)

    root = git_root(cwd_path)
    if root is None:
        sys.exit(0)

    if (root / ".clawness" / "claude-md-check-off").exists():
        sys.exit(0)

    chars, found = measure(root)
    if not found:
        sys.exit(0)

    tokens = chars // CHARS_PER_TOKEN
    if tokens < limit_tokens():
        sys.exit(0)

    # `should_ask` is called LAST, and records as it goes, so a session that would
    # have stayed quiet for any reason above doesn't spend the one shot.
    if should_ask(root, tokens):
        print(NOTE.format(tokens=tokens, chars=chars, files=", ".join(found)))
    sys.exit(0)


if __name__ == "__main__":
    main()
