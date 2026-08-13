"""Empty-coverage detection — is this project a stack Clawness knows nothing about?

Clawness ships corpus for ~29 domains. Open a session in a stack it has no rules
for — Ruby, PHP, Elixir, Haskell, C#, Swift, Dart, Scala, Clojure — and the tool
is silently near-useless: `scan_project` recognises none of the files, so the
detected domains collapse to `{general, workflows}` and nothing but the mandatory
block ever fires. The user has no signal that Clawness *could* help if they
authored a handful of project rules.

This module spots that situation structurally and lets a SessionStart note point
at `/clawness:bootstrap` (the skill that helps write a starter set). Two things it
is deliberately NOT:

* **Not a match-count heuristic.** The signal is "an uncovered stack is present
  AND `scan_project` found no corpus domain", never "few rules matched a prompt" —
  few matches is designed-normal (a signal-less prompt injects few ranked rules by
  design). Structural coverage is the honest question.
* **Not a work order.** The note this feeds orients and stops. A SessionStart note
  fires before the user has said what they came for, so it must never commission a
  session's worth of work — the trap that bit the 1.7.0 CLAUDE.md remedy and the
  early staleness note. `render_note` names the command and starts nothing.

Pure logic apart from the ledger read/write; the callers are hooks. Mirrors the
shape of `staleness.py` (per-fact ledger, atomic write, fail-silent).
"""

from __future__ import annotations

import json
import time
from pathlib import Path


# Markers for ecosystems Clawness has NO corpus domain for. Each is
# (glob_pattern, ecosystem_label). Deliberately CONSERVATIVE: only globs that
# unambiguously name one of these stacks. Ambiguous extensions are omitted on
# purpose — bare `*.m` is Objective-C as well as MATLAB, `*.c`/`*.h` are shared by
# a dozen build systems, `*.kt` co-occurs with Gradle/Java (already covered). A
# wrong "you have no coverage" note on a covered project is the false alarm that
# teaches users to ignore the note, so a missed uncovered stack is the cheaper
# error. The `has_coverage` gate below is the real backstop — this only decides
# WHICH uncovered ecosystem to name once we already know nothing is covered.
UNCOVERED_MARKERS: list[tuple[str, str]] = [
    ("Gemfile", "Ruby"),
    ("*.gemspec", "Ruby"),
    ("*.rb", "Ruby"),
    ("composer.json", "PHP"),
    ("*.php", "PHP"),
    ("mix.exs", "Elixir"),
    ("*.ex", "Elixir"),
    ("stack.yaml", "Haskell"),
    ("*.cabal", "Haskell"),
    ("*.hs", "Haskell"),
    ("*.csproj", "C#/.NET"),
    ("*.sln", "C#/.NET"),
    ("*.fsproj", "F#"),
    ("Package.swift", "Swift"),
    ("*.swift", "Swift"),
    ("pubspec.yaml", "Dart/Flutter"),
    ("*.dart", "Dart/Flutter"),
    ("build.sbt", "Scala"),
    ("*.scala", "Scala"),
    ("deps.edn", "Clojure"),
    ("project.clj", "Clojure"),
    ("*.clj", "Clojure"),
    ("build.zig", "Zig"),
    ("*.zig", "Zig"),
    ("cpanfile", "Perl"),
    ("*.pl", "Perl"),
    ("dune-project", "OCaml"),
    ("*.ml", "OCaml"),
    ("*.nimble", "Nim"),
    ("shard.yml", "Crystal"),
    ("rebar.config", "Erlang"),
]

# Domains that every project gets regardless of stack — presence of these alone is
# NOT coverage. Kept in sync with `init.scan_project`, which always adds both.
_ALWAYS_ON = frozenset({"general", "workflows"})

LEDGER_NAME = "coverage.json"


def has_coverage(domains) -> bool:
    """True when `scan_project` recognised at least one real corpus domain.

    `general` and `workflows` are added unconditionally, so a project with only
    those two is one Clawness saw no stack in — the uncovered case.
    """
    return bool(set(domains) - _ALWAYS_ON)


def detect_uncovered(project_dir: Path) -> list[str]:
    """Ecosystem labels present in *project_dir* that Clawness has no corpus for.

    Sorted, de-duplicated. A shallow top-level glob (same as `scan_project`'s
    detectors) — cheap, and a nested marker isn't the project's primary stack.
    Never raises; an unreadable directory yields [].
    """
    found: set[str] = set()
    for pattern, label in UNCOVERED_MARKERS:
        if label in found:
            continue
        try:
            if next(project_dir.glob(pattern), None) is not None:
                found.add(label)
        except OSError:
            continue
    return sorted(found)


def unasked(root: Path, labels: list[str]) -> list[str]:
    """The uncovered ecosystems this project hasn't been told about, recording as it goes.

    Keys the ledger on the *fact* (each ecosystem label), like `staleness.unasked`:
    the note is raised once per ecosystem and re-arms only when a NEW uncovered
    stack appears (a Ruby repo that later gains a Haskell sub-project asks again for
    Haskell). An unreadable ledger means "not yet asked" — warning twice costs far
    less than never warning.
    """
    if not labels:
        return []
    path = root / ".clawness" / LEDGER_NAME
    asked: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("asked"), list):
            asked = [x for x in data["asked"] if isinstance(x, str)]
    except (OSError, ValueError):
        pass

    fresh = [label for label in labels if label not in asked]
    if not fresh:
        return []

    merged = sorted(set(asked) | set(labels))
    try:
        from .plan import atomic_write_text
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps({"asked": merged, "updated": time.time()}, indent=2) + "\n",
        )
    except Exception:
        pass
    return fresh


# The SessionStart note. Its text is a tested artifact (like staleness's), because
# the failure it guards against has already happened here twice: a SessionStart
# note that reads as a work order becomes the session's task. So it orients — names
# the stack, names the command — and commissions nothing. It does NOT tell Claude
# to research the stack or author rules; `/clawness:bootstrap` does that, and only
# when the user runs it (the user typing the command IS the consent a note can't
# ask for).
_NOTE = (
    "[Clawness] No rules cover this project's stack ({labels}). Clawness has no "
    "built-in corpus for {these}, so beyond the always-on mandatory rules it is "
    "adding little here. If the user wants project-specific rules for this stack, "
    "'/clawness:bootstrap' drafts a small starter set into .clawness/rules/ from "
    "current documentation and stops for their approval before writing anything. "
    "Just mention this exists in one line — do NOT start researching or writing "
    "rules now, and do not run it unprompted. Raised once per stack. "
    "Silence with CLAW_NO_COVERAGE_NOTE=1."
)


def render_note(labels: list[str]) -> str:
    """The SessionStart note, or "" when there is nothing to say."""
    if not labels:
        return ""
    joined = ", ".join(labels)
    these = joined if len(labels) > 1 else labels[0]
    return _NOTE.format(labels=joined, these=these)
