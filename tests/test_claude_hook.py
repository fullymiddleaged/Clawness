"""
Integration tests for hooks/claude_hook.py — the UserPromptSubmit hook,
driven end-to-end via subprocess (real stdin/stdout, like Claude Code
invokes it) rather than importing internals.

Runs under pytest, or standalone:  python tests/test_claude_hook.py
"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "claude_hook.py"


def _run_hook(prompt: str, session_id: str, cwd: str, env_extra: "dict | None" = None):
    import subprocess
    env = dict(os.environ)
    env.pop("CLAW_NO_PLAN_GATE", None)
    if env_extra:
        env.update(env_extra)
    payload = json.dumps({"prompt": prompt, "session_id": session_id, "cwd": cwd})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload, capture_output=True, text=True, env=env,
    )


def _project() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".git").mkdir()
    return d


def test_first_prompt_shows_full_mandatory_block():
    root = _project()
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root))
    assert r.returncode == 0, r.stderr
    assert "# MANDATORY" in r.stdout
    assert "relevance=" not in r.stdout  # telemetry stays hidden by default


def test_second_prompt_same_session_is_abbreviated():
    root = _project()
    sid = str(uuid.uuid4())
    first = _run_hook("implement a feature", sid, str(root))
    second = _run_hook("implement another feature", sid, str(root))
    assert first.returncode == 0 and second.returncode == 0
    assert "# MANDATORY" in first.stdout
    assert "# MANDATORY" not in second.stdout
    assert "MANDATORY (in context above, still binding):" in second.stdout


def test_different_sessions_each_get_a_full_first_turn():
    root = _project()
    a = _run_hook("implement a feature", str(uuid.uuid4()), str(root))
    b = _run_hook("implement a different feature", str(uuid.uuid4()), str(root))
    assert "# MANDATORY" in a.stdout
    assert "# MANDATORY" in b.stdout


def test_full_every_one_disables_abbreviation():
    root = _project()
    sid = str(uuid.uuid4())
    _run_hook("first", sid, str(root), {"CLAW_FULL_EVERY": "1"})
    second = _run_hook("second", sid, str(root), {"CLAW_FULL_EVERY": "1"})
    assert "# MANDATORY" in second.stdout


def test_full_block_returns_on_the_sixth_prompt():
    # Default cadence is every 5th prompt AFTER the first: full on 1, 6, 11, ...
    root = _project()
    sid = str(uuid.uuid4())
    outs = [_run_hook(f"prompt {i}", sid, str(root)).stdout for i in range(1, 7)]
    assert "# MANDATORY" in outs[0]        # prompt 1
    for out in outs[1:5]:                  # prompts 2-5
        assert "# MANDATORY" not in out
    assert "# MANDATORY" in outs[5]        # prompt 6


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
