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


def _git_repo(parent: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(parent)], check=True,
                   capture_output=True, text=True)
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
