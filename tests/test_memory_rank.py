"""
Tests for retrieval-ranked project memory (clawness/memory.py).

The point of the module: a lessons log can grow without bound while per-turn cost
stays flat, because only the pinned entries plus the bullets matching this prompt
are injected.

Runs under pytest, or standalone:  python tests/test_memory_rank.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.memory import (  # noqa: E402
    clip_entries,
    parse_memory,
    rank_lessons,
    recent_lessons,
    render_memory_block,
)

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


# --- helpers --------------------------------------------------------------

def test_recent_lessons_takes_the_newest():
    assert recent_lessons(["a", "b", "c", "d"], 2) == ["c", "d"]


def test_clip_entries_keeps_the_newest_within_budget():
    kept = clip_entries(["a" * 10, "b" * 10, "c" * 10], char_budget=25)
    assert kept == ["b" * 10, "c" * 10]


def test_clip_entries_always_keeps_at_least_one():
    # A single over-budget pinned entry must not silently vanish.
    assert clip_entries(["x" * 500], char_budget=50) == ["x" * 500]


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all memory-rank tests passed")
