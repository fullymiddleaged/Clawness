"""
Tests for the SessionStart changelog check (hooks/changelog_check.py).

The load-bearing behaviour is the asymmetry: a project WITH a changelog gets a
reminder every session, a project WITHOUT one is asked exactly once, ever.

Runs under pytest, or standalone:  python tests/test_changelog_check.py
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
HOOK = REPO / "hooks" / "changelog_check.py"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _run(cwd: Path, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("CLAW_NO_CHANGELOG_CHECK", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True, text=True, env=env,
    ).stdout


def _repo(changelog: str | None = None, name: str = "CHANGELOG.md") -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], capture_output=True)
    if changelog is not None:
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(changelog, encoding="utf-8")
    return d


@needs_git
def test_existing_changelog_gets_a_reminder_and_is_named():
    out = _run(_repo("# Changelog\n\n## [Unreleased]\n"))
    assert "keeps a changelog (CHANGELOG.md)" in out
    assert "Unreleased" in out


@needs_git
def test_a_changelog_under_docs_is_found_and_named():
    out = _run(_repo("# Changelog\n", name="docs/CHANGELOG.md"))
    assert "docs/CHANGELOG.md" in out


@needs_git
def test_the_reminder_repeats_every_session():
    # Acting on it is a one-line edit, so this side is cheap to repeat — unlike
    # the question, which is not.
    root = _repo("# Changelog\n")
    assert "keeps a changelog" in _run(root)
    assert "keeps a changelog" in _run(root)


@needs_git
def test_a_missing_changelog_is_asked_about_once_and_never_again():
    root = _repo()
    first = _run(root)
    assert "has no changelog" in first
    assert "ask whether the user wants one" in first
    # The ledger closes it for good — a question re-asked every session is a
    # question nobody answers.
    assert _run(root).strip() == ""
    ledger = json.loads((root / ".clawness" / "changelog.json").read_text(encoding="utf-8"))
    assert ledger["asked"]


@needs_git
def test_the_note_never_tells_claude_to_create_it_uninvited():
    out = _run(_repo())
    assert "only if they say yes" in out
    assert "Never add one uninvited" in out


@needs_git
def test_per_project_marker_silences_it():
    root = _repo()
    (root / ".clawness").mkdir(parents=True, exist_ok=True)
    (root / ".clawness" / "changelog-check-off").write_text("", encoding="utf-8")
    assert _run(root).strip() == ""


@needs_git
def test_opt_out_env_is_silent():
    assert _run(_repo(), {"CLAW_NO_CHANGELOG_CHECK": "1"}).strip() == ""


def test_a_non_git_directory_is_silent():
    # A scratch directory has no use for a changelog.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "notes.txt").write_text("scratch\n", encoding="utf-8")
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
