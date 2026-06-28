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


def test_silent_when_nothing_detected():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "README.txt").write_text("just notes\n")
        assert _run(Path(d)).strip() == ""


def test_opt_out_is_silent():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "pyproject.toml").write_text("[project]\nname='x'\n")
        assert _run(Path(d), {"CLAW_NO_STACK_NOTE": "1"}).strip() == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
