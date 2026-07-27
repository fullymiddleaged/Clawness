"""
Tests for the concept-expansion layer and the plan gate.

Runs under pytest, or standalone:  python tests/test_semantic_and_plan.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import _tokenize, _stem, Clawness  # noqa: E402
from clawness import plan as P  # noqa: E402

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


# --- concept / stemming layer ---------------------------------------------

def test_stemming_collapses_variants():
    assert _stem("tokens") == "token"
    assert _stem("libraries") == "library"
    assert _stem("maintained") == "maintain"
    # short identifiers are left alone
    assert _stem("css") == "css"
    assert _stem("api") == "api"


def test_concept_markers_bridge_synonyms():
    # different surface words, same concept marker
    assert "__auth__" in _tokenize("login")
    assert "__auth__" in _tokenize("jwt")
    assert "__auth__" in _tokenize("authentication")
    assert "__db__" in _tokenize("postgres")
    assert "__perf__" in _tokenize("slow")
    assert "__dependency__" in _tokenize("npm")


def test_original_tokens_preserved():
    toks = _tokenize("authentication")
    assert "authentication" in toks  # exact term kept at full weight


def test_concept_bridging_in_retrieval():
    wl = Clawness(RULES_DIR)
    # query words differ from rule wording; concepts should bridge
    res = wl.retrieve("unbounded cache that keeps growing")
    assert "GEN-MEMORY-001" in res
    res = wl.retrieve("pick a well maintained npm package")
    assert "GEN-DEPS-001" in res


def test_retrieval_returns_rules():
    wl = Clawness(RULES_DIR)
    assert "[" in wl.retrieve("write tests")


# --- plan gate ------------------------------------------------------------

def _fresh_project():
    d = Path(tempfile.mkdtemp())
    (d / ".git").mkdir()  # marks project root
    return d


def test_gate_on_by_default_blocks_writes():
    root = _fresh_project()
    block, reason = P.gate_decision(root, "Write", "sess-1")
    assert block is True and "plan" in reason.lower()


def test_non_write_tools_never_gated():
    root = _fresh_project()
    assert P.gate_decision(root, "Read", "sess-1")[0] is False
    assert P.gate_decision(root, "Bash", "sess-1")[0] is False


def test_native_plan_approval_clears_session():
    root = _fresh_project()
    assert P.gate_decision(root, "Edit", "sess-A")[0] is True
    # user approves a plan in native plan mode -> ExitPlanMode recorded
    P.record_session_approval(root, "sess-A")
    assert P.gate_decision(root, "Edit", "sess-A")[0] is False
    # a different session is still gated (each session re-plans)
    assert P.gate_decision(root, "Edit", "sess-B")[0] is True


def test_no_project_local_file_can_disable_the_gate():
    """1.5.0: the per-project kill switches are gone. A project-local
    config.json/plan.json — including one left behind by an older version — must
    NOT disable the gate, because a silently-off process keeper is
    indistinguishable from a working one."""
    root = _fresh_project()
    (root / ".clawness").mkdir()
    (root / ".clawness" / "config.json").write_text(
        json.dumps({"plan_gate": {"enabled": False}}), encoding="utf-8"
    )
    (root / ".clawness" / "plan.json").write_text(
        json.dumps({"status": "approved", "approved_at": "2026-01-01T00:00:00"}),
        encoding="utf-8",
    )
    assert P.gate_decision(root, "Write", "x")[0] is True


def test_preauthorized_permission_modes_are_not_asked_again():
    """A mode that pre-authorises edits has already answered the gate's question.
    This is what makes headless behave like interactive: `claude -p
    --permission-mode acceptEdits` matches an interactive Shift+Tab session,
    because it is the same statement."""
    root = _fresh_project()
    for mode in ("acceptEdits", "auto", "dontAsk", "bypassPermissions"):
        assert P.gate_decision(root, "Write", "s", None, mode)[0] is False, mode


def test_answerable_modes_still_ask():
    """default/plan are live questions, and an unknown or missing mode falls
    through to asking — a spurious prompt costs a click, a skipped one costs the
    gate."""
    root = _fresh_project()
    for mode in ("default", "plan", "", "somethingNew", None):
        assert P.gate_decision(root, "Write", "s", None, mode)[0] is True, mode


def test_headless_planning_clears_the_gate_like_interactive():
    """Planning headlessly (--permission-mode plan → ExitPlanMode) must clear the
    gate by exactly the same path as an interactive plan approval."""
    root = _fresh_project()
    assert P.gate_decision(root, "Edit", "headless-1", None, "plan")[0] is True
    P.record_session_approval(root, "headless-1")   # what ExitPlanMode records
    assert P.gate_decision(root, "Edit", "headless-1", None, "plan")[0] is False
    assert P.gate_decision(root, "Edit", "headless-1", None, "default")[0] is False


def test_disable_via_env():
    root = _fresh_project()
    os.environ["CLAW_NO_PLAN_GATE"] = "1"
    try:
        assert P.gate_decision(root, "Write", "x")[0] is False
    finally:
        del os.environ["CLAW_NO_PLAN_GATE"]
    assert P.gate_decision(root, "Write", "x")[0] is True  # back on


def _isolated_config_dir():
    """Point the global-config lookup at a temp dir. Patches the function rather
    than CLAUDE_CONFIG_DIR so the developer's real ~/.claude can't decide the
    result. Returns (dir, restore)."""
    cfgdir = Path(tempfile.mkdtemp())
    original = P._claude_config_dirs
    P._claude_config_dirs = lambda: [cfgdir]  # type: ignore[assignment]
    return cfgdir, lambda: setattr(P, "_claude_config_dirs", original)


def test_disable_via_global_config():
    root = _fresh_project()
    cfgdir, restore = _isolated_config_dir()
    try:
        assert P.gate_decision(root, "Write", "x")[0] is True  # absent file -> ON

        target = cfgdir / "clawness" / "config.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"plan_gate": {"enabled": False}}), encoding="utf-8")
        assert P.gate_decision(root, "Write", "x")[0] is False
        # ...and it applies to every project, not just this one
        assert P.gate_decision(_fresh_project(), "Write", "y")[0] is False
    finally:
        restore()


def test_global_config_only_explicit_false_disables():
    """A corrupt or unrelated global config must leave the gate ON — failing
    toward the prompt, never toward silence."""
    root = _fresh_project()
    cfgdir, restore = _isolated_config_dir()
    try:
        target = cfgdir / "clawness" / "config.json"
        target.parent.mkdir(parents=True)
        for content in (
            "{ not json",
            json.dumps({}),
            json.dumps({"plan_gate": {}}),
            json.dumps({"plan_gate": "off"}),
            json.dumps({"plan_gate": {"enabled": "false"}}),  # string, not bool
            json.dumps({"something_else": True}),
        ):
            target.write_text(content, encoding="utf-8")
            assert P.gate_decision(root, "Write", "x")[0] is True, content
    finally:
        restore()


def test_gate_fails_open_on_bad_state():
    root = _fresh_project()
    (root / ".clawness").mkdir()
    (root / ".clawness" / "sessions.json").write_text("{ not json")
    block, _ = P.gate_decision(root, "Write", "x")
    assert isinstance(block, bool)  # never raises


def test_hook_fails_open_on_malformed_payloads():
    # The hook process must exit 0 with no traceback on any payload shape —
    # valid JSON that isn't a dict used to raise AttributeError before the
    # decision logic even ran.
    import json as _json
    import subprocess
    hook = Path(__file__).resolve().parent.parent / "hooks" / "plan_gate.py"
    for payload in ('"hi"', "[]", "42", "null", "{ not json", _json.dumps({"tool_name": 3})):
        r = subprocess.run([sys.executable, str(hook)], input=payload,
                           capture_output=True, text=True)
        assert r.returncode == 0, (payload, r.stderr)
        assert "Traceback" not in r.stderr, payload


def _run_hook(payload):
    """Drive the real plan_gate hook process; return (returncode, parsed stdout|None)."""
    import json as _json
    import subprocess
    hook = Path(__file__).resolve().parent.parent / "hooks" / "plan_gate.py"
    r = subprocess.run([sys.executable, str(hook)], input=_json.dumps(payload),
                       capture_output=True, text=True)
    out = None
    if r.stdout.strip():
        out = _json.loads(r.stdout)
    return r.returncode, out


def test_hook_prompts_with_ask_not_deny():
    # An unapproved session must PROMPT (permissionDecision="ask"), never hard-deny
    # — a deny has no in-Claude override on the VS Code build and would strand the
    # user behind a CLI the plugin doesn't install.
    root = _fresh_project()
    rc, out = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "session_id": "sess-ask",
        "cwd": str(root),
        "tool_input": {"file_path": str(root / "src" / "app.py")},
    })
    assert rc == 0
    decision = out["hookSpecificOutput"]["permissionDecision"]
    assert decision == "ask", out
    assert "plan" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_completed_edit_clears_gate_for_session():
    # Approving the first edit (a completed write carrying a tool_response) records
    # session approval, so the gate prompts at most once per session.
    root = _fresh_project()
    assert P.gate_decision(root, "Write", "sess-once")[0] is True
    rc, out = _run_hook({
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "session_id": "sess-once",
        "cwd": str(root),
        "tool_input": {"file_path": str(root / "src" / "app.py")},
        "tool_response": {"filePath": str(root / "src" / "app.py")},
    })
    assert rc == 0 and out is None  # PostToolUse emits nothing
    assert P.gate_decision(root, "Write", "sess-once")[0] is False  # now cleared


def test_declined_edit_does_not_clear_gate():
    # A PostToolUse WITHOUT a tool_response is not execution evidence (a declined
    # ask), so it must NOT settle the session as approved.
    root = _fresh_project()
    _run_hook({
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "session_id": "sess-declined",
        "cwd": str(root),
        "tool_input": {"file_path": str(root / "src" / "app.py")},
    })
    assert P.gate_decision(root, "Write", "sess-declined")[0] is True  # still gated


def test_plan_file_writes_are_never_gated():
    # The plan-mode plan file is written BEFORE approval; gating it is a
    # catch-22. It must always be exempt, even on a fresh (gate-on) project.
    root = _fresh_project()
    plan = Path.home() / ".claude" / "plans" / "demo.md"
    assert P.is_plan_file(plan) is True
    assert P.is_plan_file(root / "src" / "app.py") is False
    # a normal project edit is still blocked...
    assert P.gate_decision(root, "Write", "s", str(root / "src" / "app.py"))[0] is True
    # ...but the plan file is allowed through
    assert P.gate_decision(root, "Write", "s", str(plan))[0] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
