"""
Tests for the per-project memory (lessons-learned) injection.

Runs under pytest, or standalone:  python tests/test_memory.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import MEMORY_TEMPLATE, render_memory_block  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MEMORY_INIT = REPO / "hooks" / "memory_init.py"


def _run_memory_init(cwd: Path, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("CLAW_NO_MEMORY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(MEMORY_INIT)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True, text=True, env=env,
    )


def _write(tmp: Path, text: str) -> Path:
    p = tmp / "memory.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_renders_nothing():
    assert render_memory_block(Path(tempfile.gettempdir()) / "does-not-exist.md") == ""


def test_empty_file_renders_nothing():
    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), "   \n\n  ")
        assert render_memory_block(p) == ""


def test_content_is_wrapped_and_injected_verbatim():
    # No query -> legacy whole-file mode (the CLI path).
    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), "## Lessons\n- vitest needs --run in CI")
        block = render_memory_block(p)
        assert "CLAWNESS MEMORY" in block
        assert "vitest needs --run in CI" in block
        assert block.strip().endswith("--- END CLAWNESS MEMORY ---")
        # carries the self-maintenance nudge — kept to a single short line since
        # it re-ships every turn (ENF-MEM-001 has the full instructions)
        footer_lines = [l for l in block.splitlines() if ".clawness/memory.md" in l]
        assert len(footer_lines) == 1 and len(footer_lines[0]) < 80


def test_headings_and_html_comments_are_stripped():
    # Both are addressed to the human editing the file; shipping them cost ~107
    # tokens a turn on an otherwise EMPTY log, which is what prompted the rework.
    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), MEMORY_TEMPLATE + "- postgres needs the seed script first\n")
        block = render_memory_block(p)
        assert "<!--" not in block and "-->" not in block
        assert "# Project lessons" not in block
        assert "postgres needs the seed script first" in block


def test_untouched_template_renders_nothing():
    # A freshly bootstrapped file is all heading + comment: after stripping there
    # is no content, so it must cost zero tokens rather than ~107.
    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), MEMORY_TEMPLATE)
        assert render_memory_block(p) == ""
        assert render_memory_block(p, query="anything at all") == ""


def test_budget_keeps_the_tail_and_flags_trim():
    with tempfile.TemporaryDirectory() as d:
        lines = "\n".join(f"- lesson {i:03d}" for i in range(500))
        p = _write(Path(d), lines)
        block = render_memory_block(p, char_budget=200)
        assert "(older lessons trimmed)" in block
        # newest lessons (tail) survive; oldest are dropped
        assert "lesson 499" in block
        assert "lesson 000" not in block
        # never starts mid-bullet after trimming
        body_start = block.split("(older lessons trimmed)\n", 1)[1]
        assert body_start.lstrip().startswith("- lesson")


# --- SessionStart bootstrap hook -----------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git_repo(parent: Path, ignored: bool = True) -> Path:
    """A scratch repo. `ignored=True` pre-covers .clawness/ so the gitignore half of
    memory_init stays quiet and a test can be about the memory file alone."""
    subprocess.run(["git", "init", "-q", str(parent)], check=True,
                   capture_output=True, text=True)
    if ignored:
        (parent / ".gitignore").write_text(".clawness/\n", encoding="utf-8")
    return parent


@needs_git
def test_bootstrap_creates_memory_and_announces():
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d))
        res = _run_memory_init(repo)
        mem = repo / ".clawness" / "memory.md"
        assert mem.is_file()
        assert "## Lessons" in mem.read_text(encoding="utf-8")
        assert "[Clawness]" in res.stdout
        assert "remember this" in res.stdout


@needs_git
def test_bootstrap_is_silent_when_file_exists():
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d))
        (repo / ".clawness").mkdir()
        existing = repo / ".clawness" / "memory.md"
        existing.write_text("## Lessons\n- pre-existing\n", encoding="utf-8")
        res = _run_memory_init(repo)
        assert res.stdout.strip() == ""
        # untouched
        assert "pre-existing" in existing.read_text(encoding="utf-8")


# --- the gitignore offer -----------------------------------------------------
#
# memory.md and rules/ are meant to be committed; handoff.md and the ledgers are
# per-machine. Without a rule, a project commits the lot.

@needs_git
def test_an_uncovered_clawness_dir_gets_the_ignore_offer():
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d), ignored=False)
        out = _run_memory_init(repo).stdout
        assert "isn't covered by .gitignore" in out
        # The exact patterns, including the ones that keep the shared half tracked.
        assert ".clawness/*" in out
        assert "!.clawness/memory.md" in out
        assert "!.clawness/rules/" in out
        # Consent shape: ask, don't edit.
        assert "ask" in out
        assert "Only edit .gitignore if the user agrees" in out
        # Ignore rules don't untrack an already-committed file, and saying so is
        # the difference between the offer working and the offer looking broken.
        assert "git rm --cached" in out


@needs_git
def test_the_offer_names_the_directory_pattern_trap():
    # `.clawness/` instead of `.clawness/*` stops git descending, so the two
    # negations below it silently do nothing and memory.md is ignored after all.
    # Anyone hand-editing this block will reach for the bare directory form.
    with tempfile.TemporaryDirectory() as d:
        out = _run_memory_init(_git_repo(Path(d), ignored=False)).stdout
        assert "stops git descending" in out


@needs_git
def test_an_already_ignored_clawness_dir_is_left_alone():
    # A wholesale `.clawness/` is a decision the user made. Don't revisit it.
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d))  # writes `.clawness/`
        out = _run_memory_init(repo).stdout
        assert ".gitignore" not in out
        assert not (repo / ".clawness" / "gitignore.json").exists()


@needs_git
def test_the_offer_is_made_once_and_then_never_again():
    # Without the ledger, a "no thanks" would be re-asked every session forever.
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d), ignored=False)
        assert ".gitignore" in _run_memory_init(repo).stdout
        assert (repo / ".clawness" / "gitignore.json").is_file()
        # Nothing was written to .gitignore — the hook only ever asks.
        assert not (repo / ".gitignore").exists()
        assert _run_memory_init(repo).stdout.strip() == ""


@needs_git
def test_the_proposed_block_actually_keeps_the_shared_half_tracked():
    # The offer is only worth making if the patterns do what the note claims. Apply
    # them for real and let git adjudicate.
    from hooks.memory_init import IGNORE_BLOCK

    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d), ignored=False)
        (repo / ".gitignore").write_text(IGNORE_BLOCK, encoding="utf-8")
        (repo / ".clawness" / "rules").mkdir(parents=True)
        (repo / ".clawness" / "handoffs").mkdir(parents=True)
        for rel in ("memory.md", "handoff.md", "sessions.json",
                    "rules/PRJ-001.yml", "handoffs/2026-01-01.md"):
            (repo / ".clawness" / rel).write_text("x", encoding="utf-8")

        def ignored(rel: str) -> bool:
            return subprocess.run(
                ["git", "-C", str(repo), "check-ignore", "-q", f".clawness/{rel}"],
                capture_output=True,
            ).returncode == 0

        assert not ignored("memory.md")          # shared
        assert not ignored("rules/PRJ-001.yml")  # shared
        assert ignored("handoff.md")             # per-machine
        assert ignored("sessions.json")
        assert ignored("handoffs/2026-01-01.md")


@needs_git
def test_the_offer_still_fires_for_a_project_that_already_has_memory():
    # The two halves are independently gated: an existing memory.md means no memory
    # note, but a project set up before this shipped still needs the ignore rule.
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d), ignored=False)
        (repo / ".clawness").mkdir()
        (repo / ".clawness" / "memory.md").write_text("## Lessons\n", encoding="utf-8")
        out = _run_memory_init(repo).stdout
        assert "remember this" not in out
        assert ".gitignore" in out


@needs_git
def test_opt_out_covers_the_ignore_offer_too():
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d), ignored=False)
        res = _run_memory_init(repo, {"CLAW_NO_MEMORY": "1"})
        assert res.stdout.strip() == ""
        assert not (repo / ".clawness" / "gitignore.json").exists()


def test_a_non_git_dir_never_gets_the_ignore_offer():
    with tempfile.TemporaryDirectory() as d:
        plain = Path(d) / "nested"
        plain.mkdir()
        assert ".gitignore" not in _run_memory_init(plain).stdout


@needs_git
def test_bootstrap_opt_out_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        repo = _git_repo(Path(d))
        res = _run_memory_init(repo, {"CLAW_NO_MEMORY": "1"})
        assert res.stdout.strip() == ""
        assert not (repo / ".clawness" / "memory.md").exists()


def test_bootstrap_skips_non_git_dir():
    with tempfile.TemporaryDirectory() as d:
        plain = Path(d) / "nested"
        plain.mkdir()
        res = _run_memory_init(plain)
        assert res.stdout.strip() == ""
        assert not (plain / ".clawness" / "memory.md").exists()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all memory tests passed")
