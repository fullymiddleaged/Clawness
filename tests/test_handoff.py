"""
Tests for session handoff (clawness/handoff.py + hooks/handoff_check.py).

Runs under pytest, or standalone:  python tests/test_handoff.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.handoff import (  # noqa: E402
    HANDOFF_TEMPLATE,
    SESSION_NAME_WORDS,
    archive_handoff,
    describe_age,
    find_handoff,
    render_handoff_note,
    suggest_session_name,
)

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "handoff_check.py"

SAMPLE = """\
# Handoff — 2026-07-26

## Where we left off
Mid-refactor of clawness/core.py; render_memory_block moved to memory.py.

## State
Tests failing on budget truncation. Nothing committed.

## Next steps
Fix the tail truncation in _legacy_body.
"""


def _project(handoff: "str | None" = None, age_days: float = 0) -> Path:
    d = Path(tempfile.mkdtemp())
    if handoff is not None:
        (d / ".clawness").mkdir()
        p = d / ".clawness" / "handoff.md"
        p.write_text(handoff, encoding="utf-8")
        if age_days:
            old = time.time() - age_days * 86400
            os.utime(p, (old, old))
    return d


# --- locating -------------------------------------------------------------

def test_finds_the_handoff_file():
    root = _project(SAMPLE)
    assert find_handoff(root) == root / ".clawness" / "handoff.md"


def test_no_handoff_is_not_an_error():
    assert find_handoff(_project()) is None
    assert find_handoff("/definitely/not/a/path") is None


# --- age ------------------------------------------------------------------

def test_age_reads_naturally():
    assert describe_age(30) == "just now"
    assert describe_age(600) == "10 minutes ago"
    assert describe_age(3600) == "an hour ago"
    assert describe_age(5 * 3600) == "5 hours ago"
    assert describe_age(86400) == "yesterday"
    assert describe_age(3 * 86400) == "3 days ago"
    assert describe_age(45 * 86400) == "a month ago"


# --- session name ---------------------------------------------------------

def test_name_comes_from_the_heading_minus_the_word_every_handoff_carries():
    assert suggest_session_name(
        "# Handoff — v1.9.0 ready to release, three smoke tests outstanding\n\nbody"
    ) == "v1.9.0-ready-to-release"
    assert suggest_session_name("# auth refactor\n") == "auth-refactor"


def test_name_keeps_the_dots_in_a_version():
    # A version is the most identifying thing a heading carries; "v190" isn't it.
    assert suggest_session_name("# Handoff — v1.9.0 release") == "v1.9.0-release"


def test_name_stays_inside_the_word_budget():
    long = "# Handoff — one two three four five six seven eight"
    name = suggest_session_name(long)
    assert name.count("-") == SESSION_NAME_WORDS - 1
    assert name == "one-two-three-four"


def test_a_heading_naming_nothing_suggests_nothing():
    # The template writes `# Handoff — {date}`; with "handoff" dropped that is a bare
    # date, and `/rename 2026-08-08` is worse than staying quiet.
    assert suggest_session_name(HANDOFF_TEMPLATE.format(date="2026-08-08")) == ""
    assert suggest_session_name("no heading here at all\n") == ""
    assert suggest_session_name("") == ""
    assert suggest_session_name("## Where we left off\nnot an h1\n") == ""


def test_name_survives_punctuation_and_case():
    assert suggest_session_name("# FIX: the Login/Bug (again)!") == "fix-the-loginbug-again"


# --- rendering ------------------------------------------------------------

def test_note_carries_the_content_not_just_a_pointer():
    # A pointer costs the next session a tool call and relies on Claude choosing
    # to follow it; the content has to be right there.
    root = _project(SAMPLE)
    note = render_handoff_note(find_handoff(root))
    assert "Mid-refactor of clawness/core.py" in note
    assert "Fix the tail truncation" in note
    assert ".clawness/handoff.md" in note
    assert note.rstrip().endswith("--- END HANDOFF ---")


def test_the_note_asks_claude_to_open_with_it():
    root = _project(SAMPLE)
    note = render_handoff_note(find_handoff(root))
    assert "where the last one left off" in note
    assert "hasn't been picked up yet" in note
    assert "instead of deleting it" in note


def test_carry_on_means_start_the_work_not_interview_the_user():
    # The whole point of writing a handoff is that the next session doesn't need an
    # interview. SessionStart fires before the user's first message, so the note can't
    # know which case it's in — it has to carry BOTH branches, and the continue branch
    # has to say "start", not "summarize and wait".
    note = render_handoff_note(find_handoff(_project(SAMPLE)))
    assert "carry on" in note
    assert "go straight to Next steps and start work" in note
    assert "without an interview" in note
    # ...and the other branch survives, for a user who opens with something else.
    assert "Otherwise open the session by telling" in note


def test_the_note_bounds_questions_to_the_open_questions_section():
    # "Don't ask" is only safe because genuine blockers have somewhere to live.
    note = render_handoff_note(find_handoff(_project(SAMPLE)))
    assert "asking only what the handoff lists under Open questions" in note


def test_open_questions_survives_render_but_is_the_first_thing_truncated():
    # Truncation keeps the HEAD, and Open questions is deliberately last — so a
    # handoff long enough to be cut loses it. Pinned rather than left undefined:
    # the section is for the rare blocker, and a 2000-char handoff has bigger
    # problems than a lost question line.
    body = HANDOFF_TEMPLATE.format(date="2026-07-26").replace(
        "<none — or the decisions genuinely blocked on the user, one line each>",
        "Deploy to staging or prod first?",
    )
    short = render_handoff_note(find_handoff(_project(body)), budget=4000)
    assert "Open questions" in short
    assert "Deploy to staging or prod first?" in short

    padded = body.replace("**Open questions:**",
                          "\n".join(f"- filler {i}" for i in range(400))
                          + "\n\n**Open questions:**")
    long_note = render_handoff_note(find_handoff(_project(padded)), budget=300)
    assert "what we were doing" in long_note  # the head survives
    assert "Deploy to staging or prod first?" not in long_note
    assert "truncated" in long_note


def test_a_handoff_written_the_way_wf_handoff_001_asks_fits_the_default_budget():
    # Every other truncation test passes an explicit budget, leaving DEFAULT_BUDGET
    # unpinned. The rule asks for a short pointer — where we stopped, the next action,
    # what's uncommitted — not a status report, and one written that way must survive
    # the default render intact, including the open question that makes "carry on"
    # safe to obey.
    body = (
        "# Handoff — auth refactor\n\n"
        "We split the session store out of auth.py and got as far as step 6a of the\n"
        "plan; tokens still validate against the old table.\n\n"
        "**Next:** point `verify_token` at `sessions_v2` in clawness/auth.py, then\n"
        "`pytest tests/test_auth.py`.\n\n"
        "**Uncommitted:** auth.py, tests/test_auth.py.\n\n"
        "**Open questions:** drop the old table now or after the deploy?\n"
    )
    note = render_handoff_note(find_handoff(_project(body)))  # no explicit budget
    assert "truncated" not in note
    assert "drop the old table now or after the deploy?" in note


def test_the_note_offers_a_session_name_on_the_pickup_branch():
    # A hook can't rename the session and neither can Claude — /rename is typed by the
    # user — so the note's whole job here is to put the name in front of them once.
    note = render_handoff_note(find_handoff(_project(
        "# Handoff — v1.9.0 release\n\n**Next:** push the tag\n")))
    assert "/rename v1.9.0-release" in note
    assert "pickup branch only" in note
    assert "Say it once" in note


def test_a_date_only_heading_offers_no_name_rather_than_a_bad_one():
    # SAMPLE's heading is the template's `# Handoff — <date>`. A suggestion the user
    # has to read and reject costs more than the silence does.
    note = render_handoff_note(find_handoff(_project(SAMPLE)))
    assert "/rename" not in note
    # ...and the rest of the instruction is untouched by its absence.
    assert "go straight to Next steps and start work" in note


def test_age_is_reported_but_never_changes_the_instruction():
    # The file's existence is the state; age is information, not a branch. An old
    # handoff that nobody archived is still an outstanding handoff.
    fresh = render_handoff_note(find_handoff(_project(SAMPLE)))
    old = render_handoff_note(find_handoff(_project(SAMPLE, age_days=400)))
    assert "just now" in fresh and "months ago" in old
    for note in (fresh, old):
        assert "hasn't been picked up yet" in note
        assert "Mid-refactor of clawness/core.py" in note


def test_a_long_handoff_keeps_the_head_and_flags_the_cut():
    # Opposite of the lessons log: a handoff's summary and state are at the top.
    body = "# Handoff\n\n## Where we left off\nthe important opening line\n" + \
           "\n".join(f"- filler line {i}" for i in range(500))
    root = _project(body)
    note = render_handoff_note(find_handoff(root), budget=300)
    assert "the important opening line" in note
    assert "filler line 499" not in note
    assert "truncated" in note


def test_empty_or_missing_file_renders_nothing():
    root = _project("   \n\n")
    assert render_handoff_note(root / ".clawness" / "handoff.md") == ""
    assert render_handoff_note(root / ".clawness" / "absent.md") == ""


def test_template_prompts_for_a_short_pointer_not_a_status_report():
    # The template used to prescribe four headed sections, which is what produced
    # 30-line session recaps. It now asks for prose plus three labelled facts; Open
    # questions stays because it is what makes "carry on" safe to obey.
    filled = HANDOFF_TEMPLATE.format(date="2026-07-26")
    for label in ("Next:", "Uncommitted:", "Open questions:"):
        assert label in filled
    assert "## Where we left off" not in filled
    assert len(filled.splitlines()) <= 12


def test_non_ascii_handoff_survives_the_round_trip():
    root = _project("# Handoff\n\n## State\nrefactored the café-menu parser — done\n")
    note = render_handoff_note(find_handoff(root))
    assert "café-menu parser — done" in note


# --- archiving ------------------------------------------------------------

def test_archive_moves_the_handoff_out_of_the_live_slot():
    root = _project(SAMPLE)
    archived = archive_handoff(root)
    assert archived is not None and archived.is_file()
    assert archived.read_text(encoding="utf-8").strip() == SAMPLE.strip()
    # The live slot is now empty, so the next session sees nothing outstanding.
    assert find_handoff(root) is None


def test_archive_lands_in_the_done_directory_named_by_timestamp():
    root = _project(SAMPLE)
    archived = archive_handoff(root)
    assert archived.parent == root / ".clawness" / "handoffs" / "done"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}\.md", archived.name)


def test_archiving_twice_in_the_same_second_keeps_both():
    # Nothing is ever lost — that's the whole promise of archiving over deleting.
    root = _project(SAMPLE)
    first = archive_handoff(root, now=1_800_000_000)
    (root / ".clawness" / "handoff.md").write_text("# second\n", encoding="utf-8")
    second = archive_handoff(root, now=1_800_000_000)
    assert first != second
    assert first.is_file() and second.is_file()


def test_archive_with_nothing_to_archive_is_a_no_op():
    assert archive_handoff(_project()) is None


def test_archive_never_deletes_the_content():
    root = _project(SAMPLE)
    archived = archive_handoff(root)
    assert "Fix the tail truncation" in archived.read_text(encoding="utf-8")


# --- the SessionStart hook ------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _run_hook(cwd: Path, env_extra: "dict | None" = None):
    env = dict(os.environ)
    env.pop("CLAW_NO_HANDOFF", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True, text=True, env=env,
    )


def _git_repo(parent: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(parent)], check=True,
                   capture_output=True, text=True)
    return parent


@needs_git
def test_hook_surfaces_a_handoff_on_session_start():
    repo = _git_repo(_project(SAMPLE))
    r = _run_hook(repo)
    assert r.returncode == 0, r.stderr
    assert "[Clawness]" in r.stdout
    assert "Mid-refactor of clawness/core.py" in r.stdout


@needs_git
def test_hook_finds_the_handoff_from_a_subdirectory():
    # The user's session may start anywhere in the tree; the file is at the root.
    repo = _git_repo(_project(SAMPLE))
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    r = _run_hook(sub)
    assert "Mid-refactor of clawness/core.py" in r.stdout


@needs_git
def test_hook_is_silent_with_no_handoff():
    repo = _git_repo(_project())
    assert _run_hook(repo).stdout.strip() == ""


@needs_git
def test_hook_opt_out():
    repo = _git_repo(_project(SAMPLE))
    assert _run_hook(repo, {"CLAW_NO_HANDOFF": "1"}).stdout.strip() == ""


def test_hook_skips_a_non_git_dir():
    plain = _project(SAMPLE) / "nested"
    plain.mkdir()
    assert _run_hook(plain).stdout.strip() == ""


@needs_git
def test_hook_never_errors_on_an_unreadable_handoff():
    repo = _git_repo(_project())
    (repo / ".clawness").mkdir()
    # A directory where the file should be — read fails, hook must stay silent.
    (repo / ".clawness" / "handoff.md").mkdir()
    r = _run_hook(repo)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all handoff tests passed")
