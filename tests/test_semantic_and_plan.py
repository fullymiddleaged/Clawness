"""
Tests for the concept-expansion layer and the plan gate.

Runs under pytest, or standalone:  python tests/test_semantic_and_plan.py
"""

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


def test_manual_approve_override():
    root = _fresh_project()
    assert P.gate_decision(root, "Write", "x")[0] is True
    P.approve(root)  # session-independent fallback
    assert P.gate_decision(root, "Write", "anything")[0] is False
    P.reset(root)
    assert P.gate_decision(root, "Write", "x")[0] is True


def test_disable_via_config_and_env():
    root = _fresh_project()
    cfg = P.load_config(root)
    cfg["plan_gate"]["enabled"] = False
    P.save_config(root, cfg)
    assert P.gate_decision(root, "Write", "x")[0] is False

    # env override on a fresh (default-on) project
    root2 = _fresh_project()
    os.environ["CLAW_NO_PLAN_GATE"] = "1"
    try:
        assert P.gate_decision(root2, "Write", "x")[0] is False
    finally:
        del os.environ["CLAW_NO_PLAN_GATE"]
    assert P.gate_decision(root2, "Write", "x")[0] is True  # back on


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
