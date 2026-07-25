"""
Project memory (`.clawness/memory.md`) — parsing and retrieval.

The memory file used to be injected verbatim every full-cadence turn, capped only
by a blunt char-count tail slice. That made per-turn cost scale with how much had
ever been written, and shipped every lesson whether or not it related to the
prompt. Here we parse the file into individual entries and rank them against the
prompt, so a long log costs a flat handful of lines per turn.

Memory is ranked in its OWN pass, deliberately not merged into the rule corpus:
lessons never displace rules from `top_k`, rules never displace lessons, and
`Clawness.rank_ids` stays rule-only so `tests/ground_truth.json` and the CI eval
floors are unaffected by whatever a user writes in their memory file.

The ranking primitives (`BM25`, `TfIdfIndex`, `rrf`, `_tokenize`) are reused from
core — same concept expansion and stemming the rules get, no new dependency, and
nothing heavy in the hot path (a ~40-bullet corpus indexes in well under a ms).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .core import BM25, TfIdfIndex, _tokenize, rrf

# Section headings that mark always-injected entries. Everything else is ranked.
_PINNED_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(always|pinned)\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
# HTML comments in the file are addressed to the human reading it, not to Claude.
# They cost tokens on every turn and say nothing the model can act on, so they're
# stripped before anything else. Non-greedy + DOTALL so multi-line comments go too.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Function words are stripped before indexing memory. The rule corpus doesn't need
# this — across 113 rules, IDF drives "this"/"the"/"needs" to nothing on its own. A
# memory log is 4-40 entries, where those same words are rare enough to look
# discriminating, so without this filter "rename THIS variable" matches "BUILDKIT=1
# on THIS machine" well above the relevance floor and every prompt drags in noise.
_STOPWORDS = frozenset("""
a an the this that these those it its is are was were be been being am
and or but if then else so than as of at by for from in into on onto to with
without within about over under again once here there when where why how
i you he she we they me him her us them my your his their our
do does did done doing have has had having will would shall should can could may
might must not no nor only own same too very just now also
""".split())


def _prep(text: str) -> str:
    """Drop function words, keeping the rest of the string intact for `_tokenize`
    (which still applies stemming and concept expansion to what remains)."""
    return " ".join(t for t in text.split() if t.strip(".,;:!?()[]\"'").lower() not in _STOPWORDS)


DEFAULT_TOP_K = 3
# Higher than the rules' 0.06 floor, and deliberately so: a memory log is small
# enough that cosines run hot, and the noise tail sits much further from the real
# matches than it does across 113 rules. Measured on a 4-entry log — genuine hits
# score 0.44-0.70, incidental token overlap 0.07-0.09 — so 0.20 sits in the gap.
# A ranked lesson is unsolicited context, so the bar to spend a turn's tokens on
# it should be higher than for a rule the user's prompt actually asked about.
DEFAULT_MIN_RELEVANCE = 0.20
DEFAULT_PIN_BUDGET = 400
# Ceiling on how many (newest) lessons get ranked, so an unbounded log can't blow
# the hook's latency budget. Measured: ~0.6ms at 10 entries, ~1.6ms at 40 (the size
# ENF-MEM-001 steers toward), ~7ms at this 200 cap.
DEFAULT_MAX_ENTRIES = 200
# How many of the newest entries to show when the file just changed (see
# `force_recent` in core.render_memory_block) — a lesson written this session must
# be visible next turn even if the next prompt is about something else.
RECENT_COUNT = 3


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def parse_memory(text: str) -> tuple[list[str], list[str]]:
    """
    Split raw memory-file text into (pinned, lessons).

    Entries under an `## Always` (or `## Pinned`) heading are pinned — always
    injected. Everything else is a ranked lesson. HTML comments and heading lines
    are dropped: both are for the human editing the file.

    An entry is a `-`/`*`/`+` bullet plus any indented continuation lines. A stray
    non-bullet, non-heading line becomes its own entry rather than being dropped,
    so a user who writes plain prose still gets their content retrieved.
    """
    text = _COMMENT_RE.sub("", text)

    pinned: list[str] = []
    lessons: list[str] = []
    bucket = lessons
    current: list[str] | None = None

    def flush() -> None:
        nonlocal current
        if current:
            entry = "\n".join(current).rstrip()
            if entry.strip():
                bucket.append(entry)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _HEADING_RE.match(line):
            flush()
            bucket = pinned if _PINNED_HEADING_RE.match(line) else lessons
            continue
        if not line.strip():
            flush()
            continue
        if _BULLET_RE.match(line):
            flush()
            current = [line.strip()]
        elif current is not None and raw_line[:1].isspace():
            # Indented continuation of the bullet above.
            current.append(line.strip())
        else:
            flush()
            current = [line.strip()]
    flush()

    return pinned, lessons


def rank_lessons(
    lessons: list[str],
    query: str,
    top_k: int | None = None,
    min_relevance: float | None = None,
) -> list[str]:
    """
    Return the lessons most relevant to *query*, in file order.

    Mirrors `Clawness._rank`: BM25 and TF-IDF over the concept-expanded token
    stream, fused with RRF for ordering, then a TF-IDF-cosine floor to drop
    scattershot matches — so an unrelated prompt injects nothing rather than
    three arbitrary bullets. The domain filter and off-stack penalty don't apply
    to memory, so they're omitted.

    Results are re-sorted into file order before returning: the log is
    chronological (newest last) and preserving that reads better than relevance
    order, which would shuffle related lessons apart.
    """
    if not lessons or not query.strip():
        return []

    top_k = top_k if top_k is not None else _env_int("CLAW_MEMORY_TOP_K", DEFAULT_TOP_K)
    if min_relevance is None:
        min_relevance = _env_float("CLAW_MEMORY_MIN_RELEVANCE", DEFAULT_MIN_RELEVANCE)
    min_relevance = max(0.0, min_relevance)
    if top_k <= 0:
        return []

    prepped_query = _prep(query)
    if not prepped_query.strip():
        return []

    # Bound the work a runaway log can cause in the per-prompt hook. ENF-MEM-001
    # tells Claude to merge entries past 40, but a user's file is theirs — rank the
    # newest slice rather than letting an unbounded one blow the hook's latency.
    max_entries = _env_int("CLAW_MEMORY_MAX_ENTRIES", DEFAULT_MAX_ENTRIES)
    if max_entries > 0 and len(lessons) > max_entries:
        lessons = lessons[-max_entries:]

    docs = [_prep(entry) for entry in lessons]
    tokenized = [_tokenize(doc) for doc in docs]

    bm25 = BM25()
    bm25.build(tokenized)
    tfidf = TfIdfIndex()
    # Reuse the tokenization BM25 just did — tokenizing dominates the cost here.
    tfidf.build(docs, tokenized=tokenized)

    bm25_scores = bm25.score(_tokenize(prepped_query))
    bm25_ranked = [(i, s) for i, s in enumerate(bm25_scores) if s > 0]
    bm25_ranked.sort(key=lambda x: x[1], reverse=True)
    bm25_ranked = bm25_ranked[: top_k * 2]

    tfidf_ranked = tfidf.query(prepped_query, top_k=top_k * 2)
    tfidf_map = dict(tfidf_ranked)

    fused = rrf([bm25_ranked, tfidf_ranked])
    picked = [i for i, _ in fused if tfidf_map.get(i, 0.0) >= min_relevance][:top_k]

    return [lessons[i] for i in sorted(picked)]


def recent_lessons(lessons: list[str], count: int = RECENT_COUNT) -> list[str]:
    """The newest *count* entries (the log appends newest-last)."""
    if count <= 0:
        return []
    return lessons[-count:]


def clip_entries(entries: list[str], char_budget: int) -> list[str]:
    """
    Keep entries from the END until *char_budget* is spent.

    Used for the pinned section, which is always injected and so must not be able
    to grow unbounded. Newest-last means the tail is the freshest, matching how
    the old whole-file truncation behaved.
    """
    if char_budget <= 0:
        return []
    kept: list[str] = []
    used = 0
    for entry in reversed(entries):
        cost = len(entry) + 1
        if used + cost > char_budget and kept:
            break
        kept.append(entry)
        used += cost
        if used >= char_budget:
            break
    kept.reverse()
    return kept


def read_memory(path: str | Path) -> str:
    """Read a memory file as UTF-8. Returns "" when missing/empty/unreadable —
    callers degrade silently rather than breaking the prompt."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


HEADER = "--- CLAWNESS MEMORY (project lessons) ---"
END = "--- END CLAWNESS MEMORY ---"
# One line only — this ships every turn; ENF-MEM-001 carries the full upkeep rules.
FOOTER = "(Lesson recurs? Append one line to .clawness/memory.md.)"


def render_memory_block(
    memory_path: str | Path,
    char_budget: int = 1200,
    query: str | None = None,
    top_k: int | None = None,
    min_relevance: float | None = None,
    pin_budget: int | None = None,
    force_recent: bool = False,
) -> str:
    """
    Render a project's lessons log as an injectable block.

    Two modes:

    * *query* is None — legacy behavior: the whole file (minus HTML comments and
      headings), tail-truncated to *char_budget*. Kept for the CLI and any direct
      caller that wants to see everything.
    * *query* given — pinned entries (the `## Always` section, capped by
      *pin_budget*) plus the lessons that actually match this prompt. A long log
      then costs a flat few lines per turn instead of its whole length.

    *force_recent* additionally shows the newest entries regardless of match; the
    hook sets it on the turn the file changed, so a lesson written this session is
    never invisible on the next prompt just because the subject moved on.

    *char_budget* remains the backstop in both modes. Returns "" when the file is
    missing, empty, or unreadable — callers degrade silently.
    """
    text = read_memory(memory_path)
    if not text:
        return ""

    if pin_budget is None:
        pin_budget = _env_int("CLAW_MEMORY_PIN_BUDGET", DEFAULT_PIN_BUDGET)

    pinned, lessons = parse_memory(text)

    if query is None:
        body, truncated = _legacy_body(pinned, lessons, char_budget)
        return _wrap(
            body,
            note="(older lessons trimmed)" if truncated else "",
            tail=FOOTER,
        )

    shown = clip_entries(pinned, pin_budget)
    matched = rank_lessons(lessons, query, top_k=top_k, min_relevance=min_relevance)
    if force_recent:
        recent = recent_lessons(lessons)
        # Preserve file order and don't repeat an entry that already matched.
        matched = [e for e in lessons if e in matched or e in recent]

    lines = [f"ALWAYS: {_flatten(e)}" for e in shown]
    lines += [e if _BULLET_RE.match(e) else f"- {e}" for e in matched]

    if not lines:
        return ""

    body = "\n".join(lines)
    if len(body) > char_budget:
        body = body[:char_budget].rsplit("\n", 1)[0]

    # Exactly one trailing line, never both: when there are lessons on disk the
    # count line already points at the file, so the generic upkeep footer would be
    # a duplicate pointer. With no lessons yet, the footer is the only nudge.
    if not lessons:
        tail = FOOTER
    elif force_recent:
        # These are the newest entries, not matches — say so, or the count reads
        # as a relevance claim about a prompt they may have nothing to do with.
        tail = (
            f"({len(matched)} newest of {len(lessons)} lessons, just updated; "
            "full log in .clawness/memory.md)"
        )
    else:
        tail = (
            f"({len(matched)} of {len(lessons)} lessons matched; "
            "full log in .clawness/memory.md)"
        )
    return _wrap(body, tail=tail)


def _flatten(entry: str) -> str:
    """Collapse a (possibly multi-line) entry to one line, minus its bullet marker."""
    return " ".join(_BULLET_RE.sub("", entry).split())


def _legacy_body(
    pinned: list[str], lessons: list[str], char_budget: int
) -> tuple[str, bool]:
    """Whole-file body for the no-query path, tail-truncated to the budget."""
    body = "\n".join(pinned + lessons)
    if len(body) <= char_budget:
        return body, False
    body = body[-char_budget:]
    # Drop the partial first line so we start on a clean entry.
    nl = body.find("\n")
    if nl != -1:
        body = body[nl + 1:]
    return body, True


def _wrap(body: str, note: str = "", tail: str = "") -> str:
    """Header + optional pre-note + body + one trailing line + end marker."""
    if not body.strip():
        return ""
    parts = [HEADER]
    if note:
        parts.append(note)
    parts.append(body)
    if tail:
        # Blank line so the trailing line never reads as the last lesson entry.
        parts.append("")
        parts.append(tail)
    parts.append(END)
    return "\n".join(parts)
