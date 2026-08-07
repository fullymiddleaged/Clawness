"""Corpus staleness — is a rule still valid for the version this project runs?

The relevance floor catches unfamiliar *vocabulary*, not unfamiliar *versions*: a
major bump keeps the words ("route", "cache", "app router") and changes their
meaning, so a rule written for Next.js 14 scores like an ordinary match on a
Next.js 17 prompt and is served confidently. Nothing else in Clawness can notice
that, because nothing else records what a rule was written against.

A rule may therefore carry version provenance (loaded in `core.Rule`):

    applies_to: {"Next.js": "13-15"}
    verified: 2026-08
    sources: ["https://nextjs.org/docs/app/building-your-application/routing"]

Two invariants govern the whole feature:

* **The stamp is per rule, never per domain.** A domain-wide range is the union
  of its rules' ranges — structurally the widest claim available, and the wide
  direction is the one that fails silently (see `is_above_ceiling`).
* **Only a verified stamp arms a warning** (`is_armed`). `applies_to` alone is
  asserted, not established; it stays silent. So the feature ships doing nothing
  until real review has happened, which is the honest behaviour rather than a
  shortcoming.

Pure logic, no I/O, no network — the callers are hooks.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .init import VERSION_WATCH_JS, VERSION_WATCH_PY


# Every label a stamp may join on. The join key is the *detector's* display label
# ("Next.js"), not the package name, because that is how `scan_project` keys its
# `versions` dict. A typo'd label ("NextJS") simply never matches, so the warning
# silently never fires — a dead check that looks configured. That is why
# `clawness lint` validates membership here rather than leaving it to runtime.
WATCHED_LABELS: frozenset[str] = frozenset(
    label for _pkg, label in (*VERSION_WATCH_JS, *VERSION_WATCH_PY)
)

# Grammar: an inclusive range "13-15", or a single version "15" meaning exactly
# that one. One or two numeric components per bound, matching the shape
# `_clean_version` produces ("14", "1.4") — a third would claim a precision the
# detector cannot supply. Two components are needed, not decoration: SQLAlchemy
# 1.4-vs-2.0 and Tailwind 3.4-vs-4 are the cases the watch list exists for.
#
# An open-ended upper bound ("13-") is deliberately not expressible. It reads as
# "and everything after", which is precisely the claim nobody has evidence for
# and the one that fails silent.
_BOUND = r"\d+(?:\.\d+)?"
_RANGE_RE = re.compile(rf"^\s*({_BOUND})\s*-\s*({_BOUND})\s*$")
_SINGLE_RE = re.compile(rf"^\s*({_BOUND})\s*$")

# Stands in for "every release under this bound". A ceiling of "15" covers 15.9;
# a ceiling of "1.4" covers 1.4.x. Padding the ceiling with 0 instead would make
# "15" mean 15.0 and fire a warning on 15.1 — a false alarm on a project that is
# fine, which is how users learn to ignore the warning.
_CEILING_PAD = 1 << 30


def _bounds_text(spec: str) -> tuple[str, str] | None:
    """The two bounds of a range as the author wrote them, for display."""
    m = _RANGE_RE.match(spec or "")
    if m:
        return m.group(1), m.group(2)
    m = _SINGLE_RE.match(spec or "")
    if m:
        return m.group(1), m.group(1)
    return None


def _components(version: str) -> tuple[int, ...] | None:
    """Numeric components of a one-or-two part version, or None if unusable."""
    parts = version.strip().split(".")
    if not 1 <= len(parts) <= 2:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def parse_range(spec: str) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """Parse an `applies_to` range into (floor, ceiling) comparable tuples.

    Returns None for anything unparseable — an unusable stamp is no stamp, and
    a caller that cannot read a range must stay silent rather than guess at one.
    """
    if not spec:
        return None
    m = _RANGE_RE.match(spec)
    if m:
        low_raw, high_raw = m.group(1), m.group(2)
    else:
        m = _SINGLE_RE.match(spec)
        if not m:
            return None
        low_raw = high_raw = m.group(1)

    low = _components(low_raw)
    high = _components(high_raw)
    if low is None or high is None:
        return None

    # Floor pads with 0 ("13" starts at 13.0); ceiling pads with the sentinel.
    floor = (low + (0,))[:2]
    ceiling = (high + (_CEILING_PAD,))[:2]
    if ceiling < floor:
        return None
    return floor, ceiling


def is_above_ceiling(spec: str, detected: str) -> bool:
    """True when *detected* is past the top of *spec* — the rule may be stale.

    Only the ceiling is checked. A project running *below* a rule's floor is a
    real mismatch too, but a quieter one: the corpus is written forwards, so the
    old-project case is rarer, and warning on it would fire on every project that
    simply hasn't upgraded yet. Fails silent on any unreadable input, matching
    `_clean_version`'s "a wrong version is worse than none".
    """
    parsed = parse_range(spec)
    if parsed is None:
        return False
    found = _components(detected or "")
    if found is None:
        return False
    return (found + (0,))[:2] > parsed[1]


def is_armed(rule) -> bool:
    """True when a rule's stamp is established well enough to warn on.

    The trust invariant: `applies_to` is a claim, `verified` + `sources` are what
    make it evidence. A rule carrying a range but neither of those is asserted,
    not verified, and must stay silent — narrow on weak evidence, never widen.
    """
    return bool(rule.applies_to and rule.verified and rule.sources)


def stale_rules(rules, versions: dict[str, str]) -> list[tuple[object, str, str]]:
    """Rules whose armed stamp is exceeded by a version this project declares.

    Returns (rule, label, detected_version) triples, so a caller can report at
    rule granularity — "3 of 8 Next.js rules are unverified above 15" names what
    to distrust, where damning the whole domain does not.
    """
    if not versions:
        return []
    out: list[tuple[object, str, str]] = []
    for rule in rules:
        if not is_armed(rule):
            continue
        for label, spec in rule.applies_to.items():
            detected = versions.get(label, "")
            if detected and is_above_ceiling(spec, detected):
                out.append((rule, label, detected))
                break  # one report per rule, not one per framework it names
    return out


def summarize(stale: list[tuple[object, str, str]]) -> list[tuple[str, str, str, int]]:
    """Roll `stale_rules` output up per framework, for the note.

    Returns (label, highest_verified_bound, detected, rule_count), sorted by
    label. The count is deliberate: "3 of 8 Next.js rules" names what to
    distrust, where damning the whole domain does not.
    """
    grouped: dict[tuple[str, str], list[object]] = {}
    for rule, label, detected in stale:
        grouped.setdefault((label, detected), []).append(rule)

    out: list[tuple[str, str, str, int]] = []
    for (label, detected), rules in sorted(grouped.items()):
        best_text, best_key = "", None
        for rule in rules:
            bounds = _bounds_text(rule.applies_to.get(label, ""))
            parsed = parse_range(rule.applies_to.get(label, ""))
            if bounds is None or parsed is None:
                continue
            if best_key is None or parsed[1] > best_key:
                best_key, best_text = parsed[1], bounds[1]
        out.append((label, best_text, detected, len(rules)))
    return out


# The SessionStart note. Every clause of this is load-bearing and it has its own
# test, because the failure it guards against has already happened here: a
# SessionStart note that reads as a work order becomes the session's task. An
# earlier version of this feature had Clawness write heaps of new rules and
# degrade a session opened for something else, and 1.7.0's CLAUDE.md remedy did
# the same. A note fires BEFORE the user has said what they came for, so it can
# orient and it can license a cheap by-product — it must never commission work.
#
# Hence: no imperative to research, audit, or author rule files; the remedy is
# named but explicitly not started (the `/clawness:claude-md` shape from 1.8.0,
# where the user typing the command IS the consent); and the only write it
# permits is a one-line lesson to memory.md, passively triggered by work that is
# already happening. `.clawness/rules/` is named nowhere on purpose — that path
# is what produced the heaps.
_NOTE_HEAD = (
    "[Clawness] Version gap: {details}. Those rules' version-specific details are "
    "unverified for the version this project runs — check current documentation "
    "before relying on them, and say so rather than asserting the older API shape."
)

_NOTE_TAIL = (
    " If you happen to establish a concrete version difference while doing the "
    "user's work, append it to .clawness/memory.md as a one-line lesson — at most "
    "{backstop} this session, then stop. Do NOT go looking now: no research pass, "
    "no review of the rules, and no writing rule files. If the user wants the rules "
    "themselves brought up to date, that is '/clawness:refresh <domain>', which they "
    "run when it suits them. Raised once per framework version. "
    "Silence with CLAW_NO_STALENESS_NOTE=1."
)

# A backstop, not a target. The real bound is the passive trigger ("if you happen
# to establish… while doing the user's work"); this number only makes an
# unbounded run impossible. It is a judgment call with no measurement behind it —
# low enough to cap a runaway, high enough that a genuine major migration with
# four real gotchas isn't forced to drop two. ENF-MEM-001's 120-char cap and
# memory.py's top-3 retrieval already handle volume at injection time.
SESSION_BACKSTOP = 5


LEDGER_NAME = "staleness.json"


def unasked(root: Path, summaries: list[tuple[str, str, str, int]]) -> list:
    """The summaries this project hasn't been told about, recording them as it goes.

    **The ledger keys on the fact, not on a date**: it stores the framework label
    and the *detected major* it warned about, so the note is raised once per
    mismatch and re-arms only when the version actually moves. A "checked today"
    flag was rejected — it goes silent for the rest of the day if the user
    upgrades at 2pm having been checked at 9am, which is precisely the moment the
    warning exists for, and it re-asks forever once declined, since nothing about
    elapsed time answers the question. Same shape as `claude_md_check`, which
    re-arms on size growth rather than age.

    An unreadable ledger means "not yet asked": warning twice is a much smaller
    cost than never warning again.
    """
    path = root / ".clawness" / LEDGER_NAME
    asked: dict = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("asked"), dict):
            asked = data["asked"]
    except (OSError, ValueError):
        pass

    fresh = [s for s in summaries if asked.get(s[0]) != s[2]]
    if not fresh:
        return []

    for label, _bound, detected, _count in fresh:
        asked[label] = detected
    try:
        from .plan import atomic_write_text
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps({"asked": asked, "updated": time.time()}, indent=2) + "\n",
        )
    except Exception:
        pass
    return fresh


def render_note(summaries: list[tuple[str, str, str, int]]) -> str:
    """The SessionStart note, or "" when there is nothing to say."""
    if not summaries:
        return ""
    details = "; ".join(
        f"{count} {label} rule{'s' if count != 1 else ''} verified only up to "
        f"{bound}, but this project declares {detected}"
        for label, bound, detected, count in summaries
    )
    return _NOTE_HEAD.format(details=details) + _NOTE_TAIL.format(
        backstop=SESSION_BACKSTOP
    )
