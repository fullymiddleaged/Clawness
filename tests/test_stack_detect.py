"""
Tests for the SessionStart project-stack detection note.

Runs under pytest, or standalone:  python tests/test_stack_detect.py
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
STACK_DETECT = REPO / "hooks" / "stack_detect.py"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _run(cwd: Path, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("CLAW_NO_STACK_NOTE", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(STACK_DETECT)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True, text=True, env=env,
    ).stdout


def test_detects_python():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "pyproject.toml").write_text("[project]\nname='x'\n")
        out = _run(Path(d))
        assert "Python" in out
        assert "Detected project stack" in out


def test_detects_node_frameworks_and_infra():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "package.json").write_text('{"dependencies":{"next":"14","react":"18"}}')
        (Path(d) / "Dockerfile").write_text("FROM node\n")
        out = _run(Path(d))
        for label in ("Next.js", "React", "Docker"):
            assert label in out


def test_plain_node_app_not_mislabelled_react():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "package.json").write_text('{"dependencies":{"express":"4"}}')
        out = _run(Path(d))
        assert "TypeScript" in out      # node/ts is fair
        assert "React" not in out        # but not React


def test_declared_majors_ride_the_labels():
    # "Next.js 14" is the difference between App Router advice and Pages Router
    # advice; the bare label carries neither.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "package.json").write_text(
            '{"dependencies":{"next":"^14.2.3","react":"18.3.1"},'
            '"devDependencies":{"typescript":">=5.4.0"}}'
        )
        out = _run(Path(d))
        assert "Next.js 14.2" in out
        assert "React 18.3" in out
        assert "TypeScript 5.4" in out
        assert "MAJOR VERSIONS" in out


def test_frameworks_without_a_stack_label_still_get_versioned():
    # Pydantic and SQLAlchemy aren't stack labels of their own, but writing v1 code
    # against v2 is exactly the failure this note exists to prevent.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "requirements.txt").write_text(
            "fastapi>=0.110.0\npydantic==2.7.1\nsqlalchemy~=1.4.52\n"
        )
        out = _run(Path(d))
        assert "Pydantic 2.7" in out
        assert "SQLAlchemy 1.4" in out


def test_unreadable_version_is_omitted_not_guessed():
    # A guessed version is worse than none — it gets acted on. `*`, git URLs and
    # workspace protocols all yield nothing, and the note falls back to the label.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "package.json").write_text(
            '{"dependencies":{"react":"*","next":"github:vercel/next.js"}}'
        )
        out = _run(Path(d))
        assert "React" in out
        assert "MAJOR VERSIONS" not in out


def test_silent_when_nothing_detected():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "README.txt").write_text("just notes\n")
        assert _run(Path(d)).strip() == ""


def test_opt_out_is_silent():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "pyproject.toml").write_text("[project]\nname='x'\n")
        assert _run(Path(d), {"CLAW_NO_STACK_NOTE": "1"}).strip() == ""


# --- scan_project directly -------------------------------------------------
# Until now scan_project was only exercised through this hook by subprocess, so a
# regression in the detection tables surfaced as a confusing note-text failure.

sys.path.insert(0, str(REPO))
from clawness.init import _clean_version, _python_version, scan_project  # noqa: E402


def test_clean_version_strips_range_operators():
    assert _clean_version("^14.2.3") == "14.2"
    assert _clean_version(">=5.4.0") == "5.4"
    assert _clean_version("~1.4.52") == "1.4"
    assert _clean_version("18") == "18"
    # No numeric lead → no guess.
    for spec in ("*", "latest", "", "github:vercel/next.js", "workspace:*"):
        assert _clean_version(spec) == ""


def test_python_version_does_not_match_inside_a_longer_name():
    # "openai" must not be read out of "langchain-openai"; the same word-boundary
    # bug is why the domain scan over-detects.
    content = "langchain-openai==0.1.0\nsqlalchemy~=2.0.30\n"
    assert _python_version(content, "sqlalchemy") == "2.0"
    assert _python_version(content, "openai") == ""


def test_scan_project_reports_versions_and_domains_together():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "package.json").write_text(
            '{"dependencies":{"next":"^14.2.3"}}', encoding="utf-8"
        )
        (root / "requirements.txt").write_text("pydantic==2.7.1\n", encoding="utf-8")
        scan = scan_project(root)
        assert scan["versions"] == {"Next.js": "14.2", "Pydantic": "2.7"}
        assert {"nextjs", "react", "typescript"} <= set(scan["domains"])


def test_scan_project_survives_a_malformed_manifest():
    # Fails open: a broken package.json costs detection, never the session.
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "package.json").write_text("{not json at all", encoding="utf-8")
        scan = scan_project(root)
        assert scan["versions"] == {}
        assert "typescript" in scan["domains"]   # the file's presence still counts


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
