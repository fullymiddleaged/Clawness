"""
Tests for retrieval-ranked project memory (clawness/memory.py).

The point of the module: a lessons log can grow without bound while per-turn cost
stays flat, because only the pinned entries plus the bullets matching this prompt
are injected.

Runs under pytest, or standalone:  python tests/test_memory_rank.py
"""

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import TfIdfIndex, _tokenize  # noqa: E402
from clawness.memory import (  # noqa: E402
    _legacy_body,
    _prep,
    clip_entries,
    parse_memory,
    rank_lessons,
    read_memory,
    recent_lessons,
    render_memory_block,
)


@contextmanager
def _env(**pairs: str):
    """Set env vars for the block. Not monkeypatch: this file also runs standalone
    (`python tests/test_memory_rank.py`), where pytest fixtures don't exist."""
    old = {k: os.environ.get(k) for k in pairs}
    os.environ.update(pairs)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

SAMPLE = """\
# Project lessons (Clawness memory)
<!-- a comment for the human,
     spanning two lines -->

## Always
- unset CLAW_NO_PLAN_GATE before running pytest

## Lessons
- vitest needs --run in CI or it hangs watching files
- postgres migrations must run before the seed script
- react hooks cannot be called conditionally
- the docker build needs BUILDKIT=1 on this machine
"""


def _write(text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "memory.md"
    p.write_text(text, encoding="utf-8")
    return p


# --- parsing --------------------------------------------------------------

def test_parse_splits_pinned_from_lessons():
    pinned, lessons = parse_memory(SAMPLE)
    assert pinned == ["- unset CLAW_NO_PLAN_GATE before running pytest"]
    assert len(lessons) == 4
    assert lessons[0].startswith("- vitest")


def test_parse_drops_comments_and_headings():
    _, lessons = parse_memory(SAMPLE)
    joined = "\n".join(lessons)
    assert "<!--" not in joined
    assert "spanning two lines" not in joined
    assert "# Project lessons" not in joined


def test_parse_accepts_pinned_alias_and_is_case_insensitive():
    pinned, lessons = parse_memory("## PINNED\n- a\n\n## Lessons\n- b\n")
    assert pinned == ["- a"] and lessons == ["- b"]


def test_parse_keeps_multiline_bullets_whole():
    pinned, lessons = parse_memory("## Lessons\n- first line\n  continued here\n- second\n")
    assert lessons == ["- first line\ncontinued here", "- second"]


def test_parse_keeps_stray_prose_as_its_own_entry():
    # A user who writes plain lines instead of bullets still gets them retrieved.
    _, lessons = parse_memory("## Lessons\njust a plain line\n- a bullet\n")
    assert lessons == ["just a plain line", "- a bullet"]


def test_parse_defaults_to_lessons_without_any_heading():
    pinned, lessons = parse_memory("- no headings here\n")
    assert pinned == [] and lessons == ["- no headings here"]


# --- ranking --------------------------------------------------------------

def test_ranking_picks_the_matching_lesson():
    _, lessons = parse_memory(SAMPLE)
    hits = rank_lessons(lessons, "why does vitest hang when I run it in CI")
    assert len(hits) == 1
    assert "vitest" in hits[0]


def test_ranking_returns_nothing_for_an_unrelated_prompt():
    # The relevance floor is what stops three arbitrary bullets shipping anyway.
    _, lessons = parse_memory(SAMPLE)
    assert rank_lessons(lessons, "rename this variable to something clearer") == []


def test_ranking_respects_top_k():
    lessons = [f"- lesson about database indexing number {i}" for i in range(20)]
    assert len(rank_lessons(lessons, "database indexing", top_k=3)) == 3


def test_ranking_returns_file_order_not_relevance_order():
    _, lessons = parse_memory(SAMPLE)
    hits = rank_lessons(lessons, "docker build and postgres migrations", top_k=4)
    assert hits == [e for e in lessons if e in hits]


def test_ranking_handles_empty_inputs():
    assert rank_lessons([], "anything") == []
    assert rank_lessons(["- a"], "   ") == []


# --- ranking: thresholds and knobs ----------------------------------------
# Everything below was written against a mutation-testing pass: the module's
# comparisons and env-var names had no test that could fail on them, so a `>=`
# turned `>` or a typo in "CLAW_MEMORY_MIN_RELEVANCE" shipped green.

def _tfidf_score(lessons: list[str], query: str, index: int, top_k: int = 1) -> float:
    """The exact TF-IDF cosine `rank_lessons` gauges its floor on, so a test can sit
    precisely ON the threshold rather than safely either side of it."""
    docs = [_prep(e) for e in lessons]
    tokenized = [_tokenize(d) for d in docs]
    tfidf = TfIdfIndex()
    tfidf.build(docs, tokenized=tokenized)
    return dict(tfidf.query(_prep(query), top_k=top_k * 2))[index]


def test_floor_is_inclusive_a_lesson_scoring_exactly_the_floor_ships():
    """The comparison is `score >= min_relevance`. A lesson at 0.44 against a 0.20
    floor passes under `>` too and proves nothing — pin the equality case."""
    _, lessons = parse_memory(SAMPLE)
    q = "why does vitest hang when I run it in CI"
    score = _tfidf_score(lessons, q, index=0)
    assert rank_lessons(lessons, q, top_k=1, min_relevance=score) == [lessons[0]]
    # A hair above the same score and it must drop out — otherwise the assertion
    # above would pass with the floor ignored entirely.
    assert rank_lessons(lessons, q, top_k=1, min_relevance=score * 1.000001) == []


def test_top_k_of_zero_returns_nothing():
    # `if top_k <= 0` guards a query that would otherwise match. Note the downstream
    # slices happen to empty out at 0 too, so this pins the contract rather than the
    # guard — a `< 0` mutation of that line is not observable from outside.
    _, lessons = parse_memory(SAMPLE)
    q = "vitest hangs in CI"
    assert rank_lessons(lessons, q, top_k=0) == []
    assert rank_lessons(lessons, q, top_k=-1) == []
    assert rank_lessons(lessons, q, top_k=1) != []      # the query itself matches


def test_a_floor_of_zero_admits_anything_that_scored_at_all():
    """The floor is `>= min_relevance` against a cosine, which is never negative —
    so `max(0.0, min_relevance)` in the source is a guard against nonsense input,
    not a behaviour: 0.0 and -1.0 are the same setting. Pin what a zero floor
    actually means, rather than pretending the clamp is observable."""
    lessons = [f"- the deploy script writes a note about step {i}" for i in range(5)]
    q = "kafka consumer rebalance during a deploy"
    assert rank_lessons(lessons, q, min_relevance=0.0) != []
    assert rank_lessons(lessons, q, min_relevance=-1.0) == rank_lessons(lessons, q, min_relevance=0.0)
    assert rank_lessons(lessons, q) == []      # the default 0.20 floor rejects them


def test_env_knobs_are_read_under_the_documented_names():
    """The three CLAW_MEMORY_* names are the module's public surface. A typo in any
    of them silently ignores the user's setting — which looks identical to the
    setting having no effect."""
    _, lessons = parse_memory(SAMPLE)
    q = "docker build and postgres migrations"
    with _env(CLAW_MEMORY_TOP_K="1"):
        assert len(rank_lessons(lessons, q)) == 1
    with _env(CLAW_MEMORY_TOP_K="2"):
        assert len(rank_lessons(lessons, q)) == 2
    # A floor of 0.99 is above any real cosine, so nothing survives it.
    with _env(CLAW_MEMORY_MIN_RELEVANCE="0.99"):
        assert rank_lessons(lessons, q) == []


def test_junk_env_values_fall_back_to_the_defaults():
    """An unparseable value must land on the documented default (top-k 3), not on
    some other number — falling back to 1 would quietly halve what users see."""
    lessons = [
        "- redis cluster failover needs a sentinel quorum",
        "- redis cluster memory policy should be allkeys-lru",
        "- redis cluster failover checklist lives in ops",
        "- css grid gap is not margin",
    ]
    with _env(CLAW_MEMORY_TOP_K="lots", CLAW_MEMORY_MIN_RELEVANCE="high"):
        assert len(rank_lessons(lessons, "redis cluster failover")) == 3


# The `max_entries` window: five lessons where the STRONG matches sit at indices
# 0 and 2, so a window taken from the wrong end (`lessons[n:]` instead of
# `lessons[-n:]`) returns visibly different entries.
_WINDOW = [
    "- redis cluster failover needs a sentinel quorum",   # 0  strong
    "- css grid gap is not margin",                       # 1
    "- redis cluster failover checklist lives in ops",    # 2  strong
    "- yaml anchors break on merge keys",                 # 3
    "- tsconfig paths need an explicit baseUrl",          # 4
]
_WINDOW_Q = "redis cluster failover"


def test_max_entries_ranks_only_the_newest_slice():
    """The cap bounds hook latency on a runaway log, and it must take the NEWEST
    entries. With a cap of 2 the window is the last two, neither of which mentions
    redis — so a cap that sliced from the front would pull index 2 back in."""
    with _env(CLAW_MEMORY_MAX_ENTRIES="2"):
        assert rank_lessons(_WINDOW, _WINDOW_Q, top_k=3) == []
    newest_matches = _WINDOW[:4] + ["- redis cluster failover runbook is the newest"]
    with _env(CLAW_MEMORY_MAX_ENTRIES="2"):
        assert rank_lessons(newest_matches, _WINDOW_Q, top_k=3) == [newest_matches[-1]]


def test_max_entries_of_one_still_caps():
    # The guard is `max_entries > 0`, so a cap of exactly 1 must apply — off by one
    # there and the smallest window a user can ask for is silently ignored.
    with _env(CLAW_MEMORY_MAX_ENTRIES="1"):
        assert rank_lessons(_WINDOW, _WINDOW_Q, top_k=3) == []


def test_a_non_positive_max_entries_means_no_cap_not_an_empty_window():
    for value in ("0", "-1"):
        with _env(CLAW_MEMORY_MAX_ENTRIES=value):
            assert rank_lessons(_WINDOW, _WINDOW_Q, top_k=3) == [_WINDOW[0], _WINDOW[2]]


# --- helpers --------------------------------------------------------------

def test_recent_lessons_takes_the_newest():
    assert recent_lessons(["a", "b", "c", "d"], 2) == ["c", "d"]


def test_clip_entries_keeps_the_newest_within_budget():
    kept = clip_entries(["a" * 10, "b" * 10, "c" * 10], char_budget=25)
    assert kept == ["b" * 10, "c" * 10]


def test_clip_entries_always_keeps_at_least_one():
    # A single over-budget pinned entry must not silently vanish.
    assert clip_entries(["x" * 500], char_budget=50) == ["x" * 500]
    # ...down to a budget of 1. Only a budget of 0 or less means "keep nothing",
    # and the two cases are one character apart in the guard.
    assert clip_entries(["x" * 500], char_budget=1) == ["x" * 500]


def test_clip_entries_keeps_nothing_at_a_zero_budget():
    assert clip_entries(["a" * 10, "b" * 10], char_budget=0) == []
    assert clip_entries(["a" * 10], char_budget=-5) == []


def test_clip_entries_fits_exactly_to_the_budget():
    """Each entry costs len+1 (its newline) and the break is `used + cost > budget`,
    so two 10-char entries need exactly 22. Test on 22 and on 21: a budget of 30
    passes whatever the arithmetic is."""
    entries = ["a" * 10, "b" * 10]
    assert clip_entries(entries, char_budget=22) == entries
    assert clip_entries(entries, char_budget=21) == ["b" * 10]


def test_recent_lessons_of_zero_or_fewer_is_empty():
    assert recent_lessons(["a", "b"], 0) == []
    assert recent_lessons(["a", "b"], -1) == []


def test_read_memory_decodes_as_utf8_not_the_platform_default():
    """The repo-wide encoding gotcha, at the one place a user's own prose enters
    the prompt: on Windows a bare read defaults to cp1252 and turns an em-dash into
    mojibake at load time, then injects it every turn."""
    p = _write("## Lessons\n- the retry budget — 3 attempts — is per request\n")
    assert "— 3 attempts —" in read_memory(p)


def test_read_memory_is_silent_when_the_file_is_missing():
    assert read_memory(Path(tempfile.mkdtemp()) / "absent.md") == ""


def test_legacy_body_at_exactly_the_budget_is_not_truncated():
    """`len(body) <= char_budget` — at equality nothing is trimmed and the
    "(older lessons trimmed)" note must not appear."""
    lessons = ["- one", "- two", "- three"]
    body_len = len("\n".join(lessons))
    body, truncated = _legacy_body([], lessons, body_len)
    assert truncated is False
    assert body.splitlines() == lessons
    # One char less and it does trim — otherwise the assertion above would hold
    # even if truncation were dead code.
    _, truncated_now = _legacy_body([], lessons, body_len - 1)
    assert truncated_now is True


def test_legacy_body_keeps_the_tail_and_drops_only_the_partial_first_line():
    lessons = [f"- lesson number {i:02d}" for i in range(20)]
    body, truncated = _legacy_body([], lessons, 60)
    assert truncated is True
    kept = body.splitlines()
    assert kept[-1] == lessons[-1]          # the newest survives
    assert lessons[0] not in kept           # the oldest does not
    assert len(kept) > 1                    # only the partial line is dropped
    assert all(line in lessons for line in kept)   # no half-line left behind


# --- rendering ------------------------------------------------------------

def test_pinned_ships_even_when_nothing_matches():
    p = _write(SAMPLE)
    block = render_memory_block(p, query="rename this variable")
    assert "ALWAYS: unset CLAW_NO_PLAN_GATE before running pytest" in block
    assert "vitest" not in block
    assert "0 of 4 lessons matched" in block


def test_matching_prompt_adds_only_the_matching_lesson():
    p = _write(SAMPLE)
    block = render_memory_block(p, query="vitest hangs in CI")
    assert "vitest needs --run in CI" in block
    assert "react hooks" not in block
    assert "1 of 4 lessons matched" in block


def test_a_long_log_costs_a_flat_handful_of_lines():
    # The whole point: 200 lessons must not cost 200 lessons' worth of tokens.
    body = "## Lessons\n" + "\n".join(
        f"- lesson {i} about assorted unrelated topics" for i in range(200)
    )
    p = _write(body + "\n- vitest needs --run in CI or it hangs\n")
    block = render_memory_block(p, query="vitest hangs in CI")
    assert "vitest needs --run in CI" in block
    assert len(block) < 400


def test_force_recent_shows_newest_on_a_non_matching_prompt():
    p = _write(SAMPLE)
    block = render_memory_block(p, query="rename this variable", force_recent=True)
    assert "docker build needs BUILDKIT" in block
    assert "newest of 4 lessons, just updated" in block


def test_force_recent_does_not_duplicate_a_matched_entry():
    p = _write(SAMPLE)
    block = render_memory_block(p, query="the docker build is broken", force_recent=True)
    assert block.count("docker build needs BUILDKIT") == 1


def test_pin_budget_bounds_the_always_section():
    text = "## Always\n" + "\n".join(f"- pinned entry number {i}" for i in range(50))
    p = _write(text)
    block = render_memory_block(p, query="anything", pin_budget=60)
    assert len([l for l in block.splitlines() if l.startswith("ALWAYS:")]) <= 3


def test_char_budget_is_still_a_backstop():
    text = "## Lessons\n" + "\n".join(
        f"- database indexing note number {i}" for i in range(50)
    )
    p = _write(text)
    block = render_memory_block(p, query="database indexing", top_k=50, char_budget=120)
    body = block.split("---")[2]
    assert len(body) < 200


def test_query_mode_leaves_the_legacy_path_alone():
    p = _write(SAMPLE)
    legacy = render_memory_block(p)
    for entry in ("vitest", "postgres", "react hooks", "docker build"):
        assert entry in legacy


def test_the_trailing_count_line_is_separated_from_the_entries():
    # Without the blank line the count reads as one more lesson bullet.
    p = _write(SAMPLE)
    block = render_memory_block(p, query="vitest hangs in CI")
    lines = block.splitlines()
    tail = next(i for i, l in enumerate(lines) if l.startswith("(1 of 4"))
    assert lines[tail - 1] == ""


def test_pin_budget_env_knob_is_read_under_its_documented_name():
    text = "## Always\n" + "\n".join(f"- pinned entry number {i}" for i in range(50))
    p = _write(text)
    with _env(CLAW_MEMORY_PIN_BUDGET="60"):
        tight = render_memory_block(p, query="anything")
    with _env(CLAW_MEMORY_PIN_BUDGET="2000"):
        loose = render_memory_block(p, query="anything")
    assert tight.count("ALWAYS:") < loose.count("ALWAYS:")


def test_an_empty_lessons_section_gets_the_upkeep_footer_not_a_count():
    p = _write("## Always\n- unset CLAW_NO_PLAN_GATE before running pytest\n")
    block = render_memory_block(p, query="anything")
    assert "Append one line to .clawness/memory.md" in block
    assert "lessons matched" not in block


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all memory-rank tests passed")
