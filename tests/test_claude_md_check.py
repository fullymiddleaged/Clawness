"""
Tests for the SessionStart CLAUDE.md size check (hooks/claude_md_check.py).

Three things are load-bearing and each has to be pinned separately:

  1. It stays quiet. CLAUDE.md is a normal file to have, and a hook that comments on
     a 900-token one is noise nobody keeps switched on.
  2. When it does speak, the note makes Claude ANNOUNCE the number to the user and
     ask — it must never read as licence to start editing CLAUDE.md.
  3. The ledger re-arms on growth. A boolean would go silent at 6k and stay silent at
     30k, which is the failure this project already hit with the plan gate.

Runs under pytest, or standalone:  python tests/test_claude_md_check.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "claude_md_check.py"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

# The hook estimates 4 chars to the token and fires at 6000, so ~24,000 characters is
# the line. These sit either side of it with room to spare.
BIG = "x" * 40_000     # ~10,000 tokens
SMALL = "x" * 4_000    # ~1,000 tokens


def _run(cwd: Path, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("CLAW_NO_CLAUDE_MD_CHECK", None)
    env.pop("CLAW_CLAUDE_MD_LIMIT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True, text=True, env=env,
    ).stdout


def _repo(files: dict[str, str] | None = None) -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], capture_output=True)
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


@needs_git
def test_an_oversized_claude_md_is_reported_with_its_real_numbers():
    out = _run(_repo({"CLAUDE.md": BIG}))
    assert "CLAUDE.md" in out
    assert "10,000 tokens" in out
    assert "40,000 characters" in out
    # The estimate must be presented as one. A guess stated as fact is a number
    # people argue with instead of acting on.
    assert "roughly" in out


@needs_git
def test_a_normal_sized_claude_md_says_nothing():
    assert _run(_repo({"CLAUDE.md": SMALL})).strip() == ""


@needs_git
def test_no_claude_md_at_all_says_nothing():
    assert _run(_repo()).strip() == ""


@needs_git
def test_every_project_instruction_file_counts_toward_the_total():
    # Each of these is loaded on every turn, so the cost is the sum, not the largest.
    root = _repo({
        "CLAUDE.md": "a" * 10_000,
        "CLAUDE.local.md": "b" * 10_000,
        ".claude/CLAUDE.md": "c" * 10_000,
    })
    out = _run(root)
    assert "30,000 characters" in out
    for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"):
        assert name in out
    # None of them alone would have tripped the 24,000-char line.
    assert _run(_repo({"CLAUDE.md": "a" * 10_000})).strip() == ""


@needs_git
def test_the_note_tells_claude_to_mention_the_size_to_the_user():
    # Hooks can't prompt the user; if Claude doesn't say it, nobody hears it.
    out = _run(_repo({"CLAUDE.md": BIG}))
    assert "Mention this to the user" in out


@needs_git
def test_the_note_recommends_a_trim_and_never_performs_one():
    # 1.7.0 offered to relocate sections into .clawness/rules/ and memory.md.
    # Dogfooding that cost most of a session: a SessionStart note fires before the
    # user has said what they came for, so it cannot start a long destructive
    # refactor. Diagnosis stays here; the remedy moved behind a slash command.
    out = _run(_repo({"CLAUDE.md": BIG}))
    assert "revision pass" in out
    assert ".clawness/rules/" not in out        # the note names no destinations
    assert ".clawness/memory.md" not in out
    assert "split" not in out.lower()


@needs_git
def test_the_note_points_at_both_remedies_and_they_exist():
    # The note's whole value once it has reported the number is telling the user
    # where the fix lives. A pointer to a skill that isn't installed is worse than
    # no pointer, so pin the name against the directory it resolves from.
    out = _run(_repo({"CLAUDE.md": BIG}))
    assert "/doctor" in out                     # the harness's native trim
    assert "/clawness:claude-md" in out         # ours, for the Clawness destinations
    assert (REPO / "skills" / "claude-md" / "SKILL.md").is_file()
    skill = (REPO / "skills" / "claude-md" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: claude-md" in skill


@needs_git
def test_the_note_never_licenses_editing_claude_md_uninvited():
    out = _run(_repo({"CLAUDE.md": BIG}))
    assert "Do NOT start that work now" in out
    assert "do not reorganise the file yourself" in out
    assert "never edit CLAUDE.md uninvited" in out
    assert "the user's call" in out


@needs_git
def test_it_asks_once_and_then_stays_quiet_at_the_same_size():
    root = _repo({"CLAUDE.md": BIG})
    assert "tokens" in _run(root)
    assert _run(root).strip() == ""
    ledger = json.loads((root / ".clawness" / "claude_md.json").read_text(encoding="utf-8"))
    assert ledger["asked"]
    assert ledger["tokens"] == 10_000


@needs_git
def test_growth_below_half_again_does_not_re_ask():
    root = _repo({"CLAUDE.md": BIG})
    assert "tokens" in _run(root)
    (root / "CLAUDE.md").write_text("x" * 50_000, encoding="utf-8")  # 1.25x
    assert _run(root).strip() == ""


@needs_git
def test_growing_by_half_again_re_arms_it():
    # A boolean ledger would go silent at 6k and stay silent at 30k. An absent prompt
    # is indistinguishable from a working one, so the ledger stores the size.
    root = _repo({"CLAUDE.md": BIG})
    assert "tokens" in _run(root)
    (root / "CLAUDE.md").write_text("x" * 60_000, encoding="utf-8")  # 1.5x
    out = _run(root)
    assert "15,000 tokens" in out
    # ...and the new size becomes the baseline, so it doesn't repeat from here.
    assert _run(root).strip() == ""


@needs_git
def test_a_pre_existing_ledger_without_a_size_is_treated_as_answered():
    # Forward-compatibility with a hand-written or truncated ledger: fall back to
    # "already asked" rather than re-asking every session forever.
    root = _repo({"CLAUDE.md": BIG})
    (root / ".clawness").mkdir(parents=True, exist_ok=True)
    (root / ".clawness" / "claude_md.json").write_text(
        json.dumps({"asked": 1.0}), encoding="utf-8")
    assert _run(root).strip() == ""


@needs_git
def test_the_threshold_is_configurable():
    root = _repo({"CLAUDE.md": SMALL})
    assert _run(root).strip() == ""
    assert "tokens" in _run(root, {"CLAW_CLAUDE_MD_LIMIT": "500"})


@needs_git
def test_a_junk_threshold_falls_back_to_the_default():
    assert _run(_repo({"CLAUDE.md": BIG}), {"CLAW_CLAUDE_MD_LIMIT": "banana"})
    assert _run(_repo({"CLAUDE.md": SMALL}), {"CLAW_CLAUDE_MD_LIMIT": "0"}).strip() == ""


@needs_git
def test_per_project_marker_silences_it():
    root = _repo({"CLAUDE.md": BIG})
    (root / ".clawness").mkdir(parents=True, exist_ok=True)
    (root / ".clawness" / "claude-md-check-off").write_text("", encoding="utf-8")
    assert _run(root).strip() == ""


@needs_git
def test_opt_out_env_is_silent():
    assert _run(_repo({"CLAUDE.md": BIG}), {"CLAW_NO_CLAUDE_MD_CHECK": "1"}).strip() == ""


@needs_git
def test_the_marker_check_does_not_burn_the_one_shot():
    # Every gate above should_ask must leave the ledger untouched, or a project that
    # was silenced comes back silent after the marker is removed.
    root = _repo({"CLAUDE.md": BIG})
    (root / ".clawness").mkdir(parents=True, exist_ok=True)
    marker = root / ".clawness" / "claude-md-check-off"
    marker.write_text("", encoding="utf-8")
    _run(root)
    assert not (root / ".clawness" / "claude_md.json").exists()
    marker.unlink()
    assert "tokens" in _run(root)


def test_a_non_git_directory_is_silent():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "CLAUDE.md").write_text(BIG, encoding="utf-8")
        assert _run(Path(d)).strip() == ""


def test_unreadable_stdin_is_silent():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert r.stdout.strip() == ""
    assert r.returncode == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
