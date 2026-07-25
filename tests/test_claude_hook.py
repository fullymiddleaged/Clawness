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


def _run_hook(prompt: str, session_id: str, cwd: str, env_extra: "dict | None" = None,
              transcript: "str | None" = None):
    import subprocess
    env = dict(os.environ)
    env.pop("CLAW_NO_PLAN_GATE", None)
    if env_extra:
        env.update(env_extra)
    payload = {"prompt": prompt, "session_id": session_id, "cwd": cwd}
    if transcript:
        payload["transcript_path"] = transcript
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
    )


def _transcript(tokens: int) -> str:
    d = Path(tempfile.mkdtemp())
    p = d / "session.jsonl"
    p.write_text(json.dumps({
        "type": "assistant",
        "message": {"model": "claude-opus-5", "usage": {
            "input_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": tokens}},
    }) + "\n", encoding="utf-8")
    return str(p)


def _project(memory: "str | None" = None) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".git").mkdir()
    if memory is not None:
        (d / ".clawness").mkdir()
        (d / ".clawness" / "memory.md").write_text(memory, encoding="utf-8")
    return d


MEMORY = """\
## Always
- unset CLAW_NO_PLAN_GATE before running pytest

## Lessons
- vitest needs --run in CI or it hangs watching files
- react hooks cannot be called conditionally
"""


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


# --- project memory (retrieval-ranked) ------------------------------------

def test_memory_surfaces_the_matching_lesson():
    root = _project(MEMORY)
    sid = str(uuid.uuid4())
    _run_hook("warm up the session", sid, str(root))   # first turn forces recent
    r = _run_hook("vitest hangs in CI, why", sid, str(root))
    assert "CLAWNESS MEMORY" in r.stdout
    assert "vitest needs --run in CI" in r.stdout
    assert "react hooks" not in r.stdout


def test_unrelated_prompt_gets_pinned_only():
    root = _project(MEMORY)
    sid = str(uuid.uuid4())
    _run_hook("warm up the session", sid, str(root))
    r = _run_hook("rename a local variable for clarity", sid, str(root))
    assert "ALWAYS: unset CLAW_NO_PLAN_GATE" in r.stdout
    assert "vitest" not in r.stdout


def test_memory_ships_on_abbreviated_turns_too():
    # Memory no longer rides the mandatory cadence: it's prompt-specific and only
    # a few lines, so abbreviating it would lose the match and save nothing.
    root = _project(MEMORY)
    sid = str(uuid.uuid4())
    for i in range(4):
        _run_hook(f"warm up {i}", sid, str(root))
    r = _run_hook("vitest hangs in CI, why", sid, str(root))
    assert "# MANDATORY" not in r.stdout          # mandatory IS abbreviated
    assert "vitest needs --run in CI" in r.stdout  # memory still retrieved


def test_a_lesson_written_mid_session_shows_next_turn():
    root = _project(MEMORY)
    sid = str(uuid.uuid4())
    _run_hook("warm up the session", sid, str(root))
    _run_hook("something unrelated entirely", sid, str(root))
    mem = root / ".clawness" / "memory.md"
    mem.write_text(
        MEMORY + "- the staging deploy needs VPN access first\n", encoding="utf-8"
    )
    r = _run_hook("rename a local variable for clarity", sid, str(root))
    assert "staging deploy needs VPN access" in r.stdout
    assert "just updated" in r.stdout


def test_malformed_memory_budget_still_prints_the_rules():
    # This used to be an unguarded int(): a bad value killed the hook, so the
    # RULES block never printed either.
    root = _project(MEMORY)
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root),
                  {"CLAW_MEMORY_BUDGET": "not-a-number"})
    assert r.returncode == 0, r.stderr
    assert "# MANDATORY" in r.stdout


def test_untouched_template_costs_nothing():
    sys.path.insert(0, str(REPO))
    from clawness.core import MEMORY_TEMPLATE
    root = _project(MEMORY_TEMPLATE)
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root))
    assert "CLAWNESS MEMORY" not in r.stdout


# --- context-pressure watch -----------------------------------------------

# Pin the window so these don't depend on the machine's own settings.json.
CTX = {"CLAW_CONTEXT_LIMIT": "200000"}


def test_a_roomy_session_gets_no_context_note():
    root = _project()
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root), CTX,
                  transcript=_transcript(40_000))
    assert "CLAWNESS CONTEXT" not in r.stdout


def test_a_full_session_is_told_to_start_fresh():
    root = _project()
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root), CTX,
                  transcript=_transcript(180_000))
    assert "CLAWNESS CONTEXT" in r.stdout
    assert "fresh session" in r.stdout
    assert "90%" in r.stdout


def test_the_same_warning_does_not_repeat_every_turn():
    # A context alert stays true once reached; repeating it is how a useful
    # nudge becomes noise the user learns to ignore.
    root = _project()
    sid = str(uuid.uuid4())
    t = _transcript(180_000)
    first = _run_hook("one", sid, str(root), CTX, transcript=t)
    second = _run_hook("two", sid, str(root), CTX, transcript=t)
    assert "CLAWNESS CONTEXT" in first.stdout
    assert "CLAWNESS CONTEXT" not in second.stdout


def test_escalation_from_warn_to_urgent_still_speaks_up():
    root = _project()
    sid = str(uuid.uuid4())
    warn = _run_hook("one", sid, str(root), CTX, transcript=_transcript(145_000))
    urgent = _run_hook("two", sid, str(root), CTX, transcript=_transcript(180_000))
    assert "CLAWNESS CONTEXT" in warn.stdout and "carry on" in warn.stdout
    assert "CLAWNESS CONTEXT" in urgent.stdout and "fresh session" in urgent.stdout


def test_context_watch_can_be_disabled():
    root = _project()
    env = dict(CTX, CLAW_NO_CONTEXT_WATCH="1")
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root), env,
                  transcript=_transcript(180_000))
    assert "CLAWNESS CONTEXT" not in r.stdout


def test_a_missing_transcript_never_breaks_the_prompt():
    root = _project()
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root), CTX,
                  transcript=str(Path(tempfile.gettempdir()) / "does-not-exist.jsonl"))
    assert r.returncode == 0, r.stderr
    assert "# MANDATORY" in r.stdout
    assert "CLAWNESS CONTEXT" not in r.stdout


def test_a_corrupt_transcript_never_breaks_the_prompt():
    d = Path(tempfile.mkdtemp())
    p = d / "junk.jsonl"
    p.write_text("this is not json\n{also not\n", encoding="utf-8")
    root = _project()
    r = _run_hook("implement a feature", str(uuid.uuid4()), str(root), CTX,
                  transcript=str(p))
    assert r.returncode == 0, r.stderr
    assert "# MANDATORY" in r.stdout


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
