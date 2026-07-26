"""
Tests for the model-tier advisor (clawness/model_advisor.py).

Two halves. The unit tests pin the invariants that must never regress (tier
parsing, the opus/fable non-ordering, the upgrade/downgrade asymmetry, the ledger
semantics). The eval half scores a labeled case file and enforces a
FALSE-POSITIVE FLOOR — per LLM-EVAL-001, a heuristic with no scored baseline is
unfalsifiable, and for this feature a false positive (an unsolicited, wrong
opinion about the user's spend) is the expensive failure.

Runs under pytest, or standalone:  python tests/test_model_advisor.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness import model_advisor as M  # noqa: E402

CASES = json.loads(
    (Path(__file__).resolve().parent / "model_advisor_cases.json").read_text(
        encoding="utf-8"
    )
)


def _verdict(prompt: str, tier: int) -> str:
    advice = M.assess(prompt, tier)
    return advice.direction if advice else "silent"


# --- tier normalization ---------------------------------------------------

def test_normalize_tier_handles_every_form():
    """Aliases, full ids, and window suffixes all resolve."""
    assert M.normalize_tier("haiku") == M.TIER_LOW
    assert M.normalize_tier("claude-haiku-4-5-20251001") == M.TIER_LOW
    assert M.normalize_tier("sonnet") == M.TIER_MID
    assert M.normalize_tier("claude-sonnet-5") == M.TIER_MID
    assert M.normalize_tier("sonnet[1m]") == M.TIER_MID
    assert M.normalize_tier("opus") == M.TIER_TOP
    assert M.normalize_tier("claude-opus-5") == M.TIER_TOP
    assert M.normalize_tier("opus[1m]") == M.TIER_TOP
    assert M.normalize_tier("fable") == M.TIER_TOP
    assert M.normalize_tier("claude-fable-5") == M.TIER_TOP


def test_unknown_model_is_silent():
    """`inherit`, an unknown id, None and "" must all yield no tier — the advisor
    then says nothing rather than guessing at someone's spend."""
    deep = "redesign the architecture and migrate the database"
    for bad in ("inherit", "", None, "gpt-4", "some-future-model", 42):
        assert M.normalize_tier(bad) is None
        assert M.assess(deep, M.normalize_tier(bad)) is None
        # And defensively: a raw model string reaching assess() as a tier must
        # fail silent, not raise, since the hook composes the two calls.
        assert M.assess(deep, bad) is None


def test_opus_and_fable_are_the_same_tier():
    """There is no defensible ordering between them, so a session on either is
    never told to move — the advisor must not invent a ladder."""
    assert M.normalize_tier("opus") == M.normalize_tier("fable") == M.TIER_TOP
    deep = "design a threat model and migrate the auth layer, weighing the trade-offs"
    assert M.assess(deep, M.normalize_tier("opus")) is None
    assert M.assess(deep, M.normalize_tier("fable")) is None


# --- the asymmetry --------------------------------------------------------

def test_downgrade_never_fires_when_an_upgrade_signal_is_present():
    """The load-bearing asymmetry: a wrong downgrade is invisible harm."""
    assert _verdict("rename the helper and fix the race condition it introduces", 3) == "silent"
    assert _verdict("reformat the file and fix the typo in the deadlock handler", 3) == "silent"


def test_downgrade_requires_a_short_prompt():
    long_but_routine = (
        "rename the module and fix the typo, but first walk me through why the "
        "retrieval floor sits at 0.06 and whether that still holds once the "
        "off-stack penalty interacts with the topical floor in a bare directory"
    )
    assert _verdict(long_but_routine, 3) == "silent"


def test_upgrade_needs_two_distinct_signal_groups():
    """One signal is not enough — and two mentions of the SAME idea count once."""
    assert _verdict("optimize the slow query performance bottleneck", 2) == "silent"
    assert _verdict("optimize the bottleneck and redesign the schema", 2) == "up"


def test_very_short_prompts_are_silent():
    for p in ("continue", "yes", "go on", "ok"):
        assert M.assess(p, 2) is None
        assert M.assess(p, 3) is None


# --- ledger ---------------------------------------------------------------

def test_ledger_advises_once_per_tier_then_rearms_on_change():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert M.should_advise(root, M.TIER_MID) is True
        assert M.should_advise(root, M.TIER_MID) is False, "must not re-nag same tier"
        # A tier change re-arms it — the situation genuinely changed.
        assert M.should_advise(root, M.TIER_TOP) is True
        assert M.should_advise(root, M.TIER_TOP) is False
        # ...and the original tier stays recorded.
        assert M.should_advise(root, M.TIER_MID) is False


def test_ledger_survives_a_corrupt_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path = root / ".clawness" / "model_advice.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert M.should_advise(root, M.TIER_MID) is True  # must not raise


# --- rendering ------------------------------------------------------------

def test_render_hands_over_evidence_and_licenses_silence():
    """The note must give Claude the signals AND explicit permission to say
    nothing — that licence is what makes a wrong heuristic cheap."""
    advice = M.assess("design a threat model and migrate the auth layer", 2)
    assert advice is not None
    note = M.render_advice(advice)
    assert "CLAWNESS MODEL CHECK" in note
    assert "security" in note and "migration" in note   # the reasons, by name
    assert "say nothing" in note
    assert "/model" in note                              # how to act on it
    assert M.render_advice(None) == ""


# --- eval: the false-positive floor ---------------------------------------

def test_eval_no_false_positives_on_routine_prompts():
    """CI floor: ZERO routine prompts may trigger advice, on either tier."""
    fired = [
        (p, tier, _verdict(p, tier))
        for p in CASES["silent_routine"]["prompts"]
        for tier in (M.TIER_MID, M.TIER_TOP)
        if _verdict(p, tier) != "silent"
    ]
    assert not fired, f"false positives on routine work: {fired}"


def test_eval_guarded_cases_stay_silent():
    for case in CASES["silent_guarded"]["cases"]:
        got = _verdict(case["prompt"], case["tier"])
        assert got == "silent", f"{case['why']}: got {got!r} for {case['prompt']!r}"


def test_eval_recall_on_upgrade_and_downgrade():
    """The positive half — the feature has to actually fire when it should."""
    for case in CASES["upgrade"]["cases"]:
        got = _verdict(case["prompt"], case["tier"])
        assert got == "up", f"expected 'up', got {got!r} for {case['prompt']!r}"
    for case in CASES["downgrade"]["cases"]:
        got = _verdict(case["prompt"], case["tier"])
        assert got == "down", f"expected 'down', got {got!r} for {case['prompt']!r}"


# --- end to end: the two hooks that carry model -> task across events ------

import os        # noqa: E402
import subprocess  # noqa: E402
import uuid      # noqa: E402

REPO = Path(__file__).resolve().parent.parent
STACK_HOOK = REPO / "hooks" / "stack_detect.py"
PROMPT_HOOK = REPO / "hooks" / "claude_hook.py"
DEEP = "design a threat model for the auth layer and migrate it, weighing the trade-offs"


def _env(**extra) -> dict:
    env = dict(os.environ)
    env.pop("CLAW_NO_PLAN_GATE", None)
    env.pop("CLAW_NO_MODEL_ADVISOR", None)
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


def _project() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".clawness").mkdir()
    return d


def _session_start(session_id: str, cwd: Path, model, env: dict):
    payload = {"session_id": session_id, "cwd": str(cwd),
               "hook_event_name": "SessionStart", "source": "startup"}
    if model is not None:
        payload["model"] = model
    return subprocess.run([sys.executable, str(STACK_HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def _prompt(session_id: str, cwd: Path, text: str, env: dict):
    payload = {"prompt": text, "session_id": session_id, "cwd": str(cwd)}
    return subprocess.run([sys.executable, str(PROMPT_HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def test_end_to_end_model_from_sessionstart_payload():
    """The happy path: SessionStart stashes the model, prompt 1 acts on it."""
    sid, proj, env = str(uuid.uuid4()), _project(), _env()
    assert _session_start(sid, proj, "claude-sonnet-5", env).returncode == 0
    out = _prompt(sid, proj, DEEP, env).stdout
    assert "CLAWNESS MODEL CHECK" in out
    assert "say nothing" in out       # Claude keeps the final call


def test_end_to_end_falls_back_to_settings_when_payload_omits_model():
    """The `model` field is documented as optional, so the settings fallback is
    a real code path, not a belt-and-braces extra."""
    cfg = Path(tempfile.mkdtemp())
    (cfg / "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    sid, proj = str(uuid.uuid4()), _project()
    env = _env(CLAUDE_CONFIG_DIR=str(cfg))
    assert _session_start(sid, proj, None, env).returncode == 0
    assert "CLAWNESS MODEL CHECK" in _prompt(sid, proj, DEEP, env).stdout


def test_end_to_end_silent_on_later_prompts():
    """Only the first prompt may raise it — never mid-session."""
    sid, proj, env = str(uuid.uuid4()), _project(), _env()
    _session_start(sid, proj, "sonnet", env)
    assert "CLAWNESS MODEL CHECK" in _prompt(sid, proj, DEEP, env).stdout
    assert "CLAWNESS MODEL CHECK" not in _prompt(sid, proj, DEEP, env).stdout


def test_end_to_end_opt_out_and_unknown_model_are_silent():
    sid, proj = str(uuid.uuid4()), _project()
    env = _env(CLAW_NO_MODEL_ADVISOR="1")
    _session_start(sid, proj, "sonnet", env)
    assert "CLAWNESS MODEL CHECK" not in _prompt(sid, proj, DEEP, env).stdout

    # An unrecognized model must also stay silent, with no settings to fall back on.
    cfg = Path(tempfile.mkdtemp())
    sid2, proj2 = str(uuid.uuid4()), _project()
    env2 = _env(CLAUDE_CONFIG_DIR=str(cfg))
    _session_start(sid2, proj2, "some-unknown-model", env2)
    assert "CLAWNESS MODEL CHECK" not in _prompt(sid2, proj2, DEEP, env2).stdout


def test_end_to_end_routine_prompt_stays_silent():
    """The case that must hold in the wild: normal work, no opinion offered."""
    sid, proj, env = str(uuid.uuid4()), _project(), _env()
    _session_start(sid, proj, "sonnet", env)
    out = _prompt(sid, proj, "add a test for parse_memory", env).stdout
    assert "CLAWNESS MODEL CHECK" not in out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("done")
