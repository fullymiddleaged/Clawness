"""Empty-coverage detection: the structural signal, the note text, the ledger,
and the whole thing driven through the real stack_detect hook."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from clawness.coverage import (
    UNCOVERED_MARKERS,
    detect_uncovered,
    has_coverage,
    render_note,
    unasked,
)

REPO = Path(__file__).resolve().parent.parent
STACK_DETECT = REPO / "hooks" / "stack_detect.py"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

COVERAGE_MARK = "No rules cover this project's stack"


# ── has_coverage: the gate ────────────────────────────────────────

class TestHasCoverage:
    def test_only_always_on_domains_is_not_coverage(self):
        assert has_coverage({"general", "workflows"}) is False

    def test_empty_is_not_coverage(self):
        assert has_coverage(set()) is False

    def test_any_real_domain_is_coverage(self):
        assert has_coverage({"python", "general"}) is True
        assert has_coverage({"general", "workflows", "bash"}) is True

    def test_accepts_a_list_too(self):
        assert has_coverage(["general", "workflows"]) is False
        assert has_coverage(["general", "rust"]) is True


# ── detect_uncovered: which uncovered stack, if any ───────────────

class TestDetectUncovered:
    def test_empty_directory_finds_nothing(self, tmp_path):
        assert detect_uncovered(tmp_path) == []

    def test_a_gemfile_is_ruby(self, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'x'\n", encoding="utf-8")
        assert detect_uncovered(tmp_path) == ["Ruby"]

    def test_source_extension_also_matches(self, tmp_path):
        (tmp_path / "main.hs").write_text("main = pure ()\n", encoding="utf-8")
        assert detect_uncovered(tmp_path) == ["Haskell"]

    def test_a_label_is_reported_once_not_per_glob(self, tmp_path):
        # Gemfile AND *.rb both map to Ruby — one label, not two.
        (tmp_path / "Gemfile").write_text("x\n", encoding="utf-8")
        (tmp_path / "app.rb").write_text("puts 1\n", encoding="utf-8")
        assert detect_uncovered(tmp_path) == ["Ruby"]

    def test_a_polyglot_dir_returns_a_sorted_union(self, tmp_path):
        (tmp_path / "mix.exs").write_text("x\n", encoding="utf-8")
        (tmp_path / "Gemfile").write_text("x\n", encoding="utf-8")
        assert detect_uncovered(tmp_path) == ["Elixir", "Ruby"]

    def test_covered_stack_files_are_not_uncovered_markers(self, tmp_path):
        # A Python file must not read as an uncovered stack — that is has_coverage's
        # job, but detect_uncovered must not itself claim Python/Go/etc.
        (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
        assert detect_uncovered(tmp_path) == []

    def test_every_marker_carries_a_nonempty_label(self):
        for glob, label in UNCOVERED_MARKERS:
            assert glob and label


# ── The note text: it orients, it never commissions work ──────────

class TestNoteText:
    @pytest.fixture
    def note(self):
        return render_note(["Ruby"])

    def test_empty_labels_means_no_note(self):
        assert render_note([]) == ""

    def test_it_names_the_stack(self, note):
        assert "Ruby" in note

    def test_it_names_the_bootstrap_command(self, note):
        assert "/clawness:bootstrap" in note

    def test_it_commissions_no_work(self, note):
        low = note.lower()
        # It must not read as a work order: no imperative to start researching or
        # writing rules now. This is the trap that ate sessions twice.
        assert "do not start" in low or "do not run it unprompted" in low
        assert "do not start researching" in low

    def test_it_carries_its_own_opt_out(self, note):
        assert "CLAW_NO_COVERAGE_NOTE" in note

    def test_several_stacks_are_all_named(self):
        note = render_note(["Elixir", "Ruby"])
        assert "Elixir" in note and "Ruby" in note


# ── The ledger: once per stack, re-arming on a new one ────────────

class TestLedger:
    def test_a_first_note_passes_and_a_repeat_does_not(self, tmp_path):
        assert unasked(tmp_path, ["Ruby"]) == ["Ruby"]
        assert unasked(tmp_path, ["Ruby"]) == []

    def test_the_ledger_file_is_written(self, tmp_path):
        unasked(tmp_path, ["Ruby"])
        data = json.loads((tmp_path / ".clawness" / "coverage.json").read_text("utf-8"))
        assert "Ruby" in data["asked"]

    def test_a_new_uncovered_stack_re_arms(self, tmp_path):
        assert unasked(tmp_path, ["Ruby"]) == ["Ruby"]
        # A Haskell sub-project appears later — it hasn't been asked about.
        assert unasked(tmp_path, ["Haskell", "Ruby"]) == ["Haskell"]

    def test_stacks_are_tracked_independently(self, tmp_path):
        unasked(tmp_path, ["Ruby"])
        assert unasked(tmp_path, ["Elixir"]) == ["Elixir"]

    def test_no_labels_writes_no_ledger(self, tmp_path):
        assert unasked(tmp_path, []) == []
        assert not (tmp_path / ".clawness" / "coverage.json").exists()

    def test_an_unreadable_ledger_asks_rather_than_going_silent(self, tmp_path):
        led = tmp_path / ".clawness" / "coverage.json"
        led.parent.mkdir(parents=True)
        led.write_text("{ not json", encoding="utf-8")
        assert unasked(tmp_path, ["Ruby"]) == ["Ruby"]


# ── End to end, through the real hook ─────────────────────────────

def _project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True)
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    return root


def _run_hook(root: Path, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    for var in ("CLAW_NO_STACK_NOTE", "CLAW_NO_COVERAGE_NOTE", "CLAW_NO_STALENESS_NOTE"):
        env.pop(var, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(STACK_DETECT)],
        input=json.dumps({"cwd": str(root)}),
        capture_output=True, text=True, env=env,
    ).stdout


@needs_git
class TestThroughTheHook:
    def test_an_uncovered_stack_is_flagged_once(self):
        root = _project({"Gemfile": "source 'x'\n", "app.rb": "puts 1\n"})

        first = _run_hook(root)
        assert COVERAGE_MARK in first
        assert "Ruby" in first
        # Once per stack, not once per session.
        assert COVERAGE_MARK not in _run_hook(root)

    def test_a_covered_stack_stays_silent(self):
        root = _project({"main.py": "print(1)\n"})
        out = _run_hook(root)
        assert COVERAGE_MARK not in out
        # ...and the ordinary stack note still fires for it.
        assert "Detected project stack" in out

    def test_a_polyglot_with_some_coverage_is_not_flagged(self):
        # Python + Ruby: coverage exists (Python), so no empty-coverage note.
        root = _project({"main.py": "print(1)\n", "Gemfile": "source 'x'\n"})
        assert COVERAGE_MARK not in _run_hook(root)

    def test_the_opt_out_silences_it(self):
        root = _project({"Gemfile": "source 'x'\n"})
        assert COVERAGE_MARK not in _run_hook(root, {"CLAW_NO_COVERAGE_NOTE": "1"})

    def test_an_empty_git_dir_stays_silent(self):
        root = _project({})
        assert COVERAGE_MARK not in _run_hook(root)
