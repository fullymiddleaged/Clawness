"""
Tests for the context-pressure watch (clawness/context_watch.py).

Runs under pytest, or standalone:  python tests/test_context_watch.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.context_watch import (  # noqa: E402
    MIN_TOKENS_TO_REPORT,
    Alert,
    Usage,
    assess,
    declared_window,
    find_transcript,
    infer_limit,
    limit_from_settings,
    native_window,
    read_context,
    read_context_tokens,
    render_alert,
)
from clawness.session_state import context_snapshot, should_alert_context  # noqa: E402


def _transcript(*entries: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "session.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )
    return p


def _assistant(input_t=0, cache_create=0, cache_read=0) -> dict:
    return {
        "type": "assistant",
        "message": {
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": input_t,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


class _Env:
    """Set env vars for a block, restoring whatever was there."""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --- reading the transcript ----------------------------------------------

def test_reads_the_live_context_size():
    # input + cache_creation + cache_read IS the prompt that was just sent.
    p = _transcript(_assistant(2, 1987, 144253))
    assert read_context_tokens(p) == 146242


def test_uses_the_most_recent_usage_record():
    p = _transcript(_assistant(0, 0, 10_000), _assistant(0, 0, 90_000))
    assert read_context_tokens(p) == 90_000


def test_skips_entries_without_usage():
    p = _transcript(_assistant(0, 0, 50_000), {"type": "user", "message": {}})
    assert read_context_tokens(p) == 50_000


def test_missing_or_empty_transcript_reads_none():
    assert read_context_tokens(Path(tempfile.gettempdir()) / "nope.jsonl") is None
    empty = Path(tempfile.mkdtemp()) / "e.jsonl"
    empty.write_text("", encoding="utf-8")
    assert read_context_tokens(empty) is None


def test_corrupt_lines_do_not_break_the_read():
    d = Path(tempfile.mkdtemp())
    p = d / "s.jsonl"
    p.write_text(
        "not json at all\n" + json.dumps(_assistant(0, 0, 70_000)) + "\n{ broken\n",
        encoding="utf-8",
    )
    assert read_context_tokens(p) == 70_000


def test_reads_only_the_tail_of_a_huge_transcript():
    # Transcripts reach several MB; this runs on every prompt.
    d = Path(tempfile.mkdtemp())
    p = d / "big.jsonl"
    filler = json.dumps({"type": "user", "message": {"content": "x" * 2000}})
    with open(p, "w", encoding="utf-8") as fh:
        for _ in range(2000):
            fh.write(filler + "\n")
        fh.write(json.dumps(_assistant(0, 0, 123_000)) + "\n")
    assert p.stat().st_size > 3_000_000
    assert read_context_tokens(p) == 123_000


# --- inferring the window -------------------------------------------------

def test_explicit_limit_wins():
    with _Env(CLAW_CONTEXT_LIMIT="123456"):
        assert infer_limit(50_000) == 123456


def test_settings_model_marks_a_1m_window():
    cfg = Path(tempfile.mkdtemp())
    (cfg / "settings.json").write_text(json.dumps({"model": "opus[1m]"}), encoding="utf-8")
    with _Env(CLAUDE_CONFIG_DIR=str(cfg), CLAW_CONTEXT_LIMIT=None):
        assert limit_from_settings() == 1_000_000
        # Without this, a 1M session would false-alarm through 140k-200k.
        assert infer_limit(160_000) == 1_000_000


def test_settings_local_takes_precedence():
    cfg = Path(tempfile.mkdtemp())
    (cfg / "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    (cfg / "settings.local.json").write_text(
        json.dumps({"model": "opus[1m]"}), encoding="utf-8"
    )
    with _Env(CLAUDE_CONFIG_DIR=str(cfg)):
        assert limit_from_settings() == 1_000_000


def test_plain_model_falls_back_to_the_small_window():
    cfg = Path(tempfile.mkdtemp())
    (cfg / "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    with _Env(CLAUDE_CONFIG_DIR=str(cfg), CLAW_CONTEXT_LIMIT=None):
        assert limit_from_settings() is None
        assert infer_limit(50_000) == 200_000


def test_observed_usage_corrects_an_understated_window():
    cfg = Path(tempfile.mkdtemp())  # no settings file at all
    with _Env(CLAUDE_CONFIG_DIR=str(cfg), CLAW_CONTEXT_LIMIT=None):
        # A session sitting at 300k is self-evidently not a 200k session.
        assert infer_limit(300_000) == 1_000_000


def test_native_1m_models_are_recognised_at_the_version_boundary():
    with _Env(CLAUDE_CODE_DISABLE_1M_CONTEXT=None):
        # Docs: "Fable 5.1, Fable 5, Sonnet 5 and later, and Opus 4.7 and later".
        assert native_window("claude-opus-4-7") == 1_000_000
        assert native_window("claude-opus-4-6") is None
        assert native_window("claude-opus-5-5") == 1_000_000
        assert native_window("claude-sonnet-5") == 1_000_000
        assert native_window("claude-sonnet-4-6") is None
        assert native_window("claude-fable-5-1") == 1_000_000
        assert native_window("claude-mythos-5-1") == 1_000_000
        assert native_window("claude-haiku-4-5") is None
        # A date suffix is not a minor version.
        assert native_window("claude-sonnet-4-20250514") is None
        # Bedrock-shaped ids resolve too.
        assert native_window("us.anthropic.claude-opus-4-7-v1:0") == 1_000_000
        assert native_window("") is None
        assert native_window(None) is None


def test_disable_1m_env_turns_native_detection_off():
    with _Env(CLAUDE_CODE_DISABLE_1M_CONTEXT="1"):
        assert native_window("claude-opus-5-5") is None


def test_transcript_model_prevents_the_200k_false_alarm():
    # The live case that motivated this: an Opus 5.5 session at ~152k was told
    # it was 76% full of an assumed 200k window. Since 1.21 the alert is keyed
    # to the compaction line (200k idle floor on a 1M model), so 140k — 70% of
    # that — is the quiet point that still separates the two windows.
    cfg = Path(tempfile.mkdtemp())
    with _Env(CLAUDE_CONFIG_DIR=str(cfg), CLAW_CONTEXT_LIMIT=None,
              CLAUDE_CODE_DISABLE_1M_CONTEXT=None):
        assert infer_limit(152_000, model="claude-opus-5-5") == 1_000_000
        assert assess(140_000, 138_000, model="claude-opus-5-5") is None
        # A model with an opt-in window still assumes the small one.
        assert infer_limit(152_000, model="claude-haiku-4-5") == 200_000
        assert assess(140_000, 138_000, model="claude-haiku-4-5").level == "warn"


_DECLARED_OFF = dict(CLAUDE_CODE_MAX_CONTEXT_TOKENS=None, DISABLE_COMPACT=None,
                     CLAUDE_CODE_DISABLE_1M_CONTEXT=None, CLAW_CONTEXT_LIMIT=None)


def test_declared_window_applies_to_a_non_claude_model():
    # GLM over OpenRouter: the user told Claude Code the window, so Clawness
    # measures against the same number instead of the 200k guess.
    cfg = Path(tempfile.mkdtemp())
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CONFIG_DIR": str(cfg),
                 "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "400000"}):
        assert declared_window("z-ai/glm-5.3") == 400_000
        assert infer_limit(150_000, model="z-ai/glm-5.3") == 400_000
        # 400k window: the line is the 200k idle floor, and 140k is 70% of it.
        # Under the 200k guess the line would be 167k, and 140k would warn.
        assert assess(140_000, 139_000, model="z-ai/glm-5.3") is None
        # No usage record read yet: still honoured.
        assert declared_window(None) == 400_000


def test_declared_window_accepts_the_documented_number_forms():
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "400_000"}):
        assert declared_window("glm-5.3") == 400_000
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "4e5"}):
        assert declared_window("glm-5.3") == 400_000
    for bad in ("", "lots", "0", "-5"):
        with _Env(**{**_DECLARED_OFF, "CLAUDE_CODE_MAX_CONTEXT_TOKENS": bad}):
            assert declared_window("glm-5.3") is None


def test_declared_window_is_ignored_where_claude_code_ignores_it():
    # A leftover variable must not shrink a native-1M Opus session: Claude Code
    # honours it on a recognized Claude id only under DISABLE_COMPACT.
    cfg = Path(tempfile.mkdtemp())
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CONFIG_DIR": str(cfg),
                 "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "300000"}):
        assert declared_window("claude-opus-5-5") is None
        assert declared_window("anthropic/claude-sonnet-5") is None
        assert infer_limit(152_000, model="claude-opus-5-5") == 1_000_000
        # An unrecognized id carrying [1m] is sized 1M by Claude Code instead.
        assert declared_window("my-gateway-model[1m]") is None
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "300000",
                 "DISABLE_COMPACT": "1"}):
        assert declared_window("claude-opus-5-5") == 300_000
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "300000",
                 "CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}):
        assert declared_window("my-gateway-model[1m]") == 300_000


def test_claw_context_limit_still_beats_the_declared_window():
    with _Env(**{**_DECLARED_OFF, "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "400000",
                 "CLAW_CONTEXT_LIMIT": "128000"}):
        assert infer_limit(50_000, model="glm-5.3") == 128_000


def test_read_context_returns_the_model_alongside_tokens():
    p = _transcript(_assistant(0, 0, 80_000))
    assert read_context(p) == (80_000, "claude-opus-5")


def test_missing_settings_file_is_not_an_error():
    with _Env(CLAUDE_CONFIG_DIR=str(Path(tempfile.mkdtemp()) / "absent")):
        assert limit_from_settings() is None


# --- assessing ------------------------------------------------------------

def test_a_fresh_session_says_nothing():
    assert assess(MIN_TOKENS_TO_REPORT - 1, 0, limit=200_000) is None
    assert assess(50_000, 45_000, limit=200_000) is None


def test_warn_and_urgent_thresholds():
    assert assess(140_000, 135_000, limit=200_000).level == "warn"
    assert assess(175_000, 170_000, limit=200_000).level == "urgent"


def test_the_thresholds_fire_exactly_on_the_boundary():
    """The documented contract is 'at 85%', not 'somewhere past it'. The cases
    above sit NEAR the thresholds (175k is 87.5%), so >= could weaken to > with
    nothing noticing; these land exactly on them."""
    assert assess(170_000, 165_000, limit=200_000).level == "urgent"   # exactly 85%
    assert assess(140_000, 135_000, limit=200_000).level == "warn"     # exactly 70%


def test_a_surge_fires_at_the_exact_size_and_headroom_limits():
    """24k added is exactly 12% of a 200k window, and 120k of room at that rate
    is exactly 5 turns — the two boundaries of the surge condition at once."""
    a = assess(80_000, 56_000, limit=200_000)
    assert a is not None and a.level == "surge"
    assert a.added == 24_000 and a.turns_left == 5


def test_a_surge_fires_below_the_warn_threshold():
    # 40k added at 60k used: comfortable percentage, but only ~3 turns of room.
    a = assess(60_000, 20_000, limit=200_000)
    assert a.level == "surge"
    assert a.added == 40_000 and a.turns_left == 3


def test_a_surge_with_plenty_of_room_stays_quiet():
    # Same jump against a 1M window implies 23 turns left — not worth saying.
    assert assess(60_000, 20_000, limit=1_000_000) is None


def test_no_previous_measurement_still_reports_level():
    a = assess(180_000, 0, limit=200_000)
    assert a.level == "urgent" and a.added == 0 and a.turns_left is None


def test_thresholds_are_tunable():
    with _Env(CLAW_CONTEXT_WARN="0.5", CLAW_CONTEXT_URGENT="0.6"):
        assert assess(105_000, 100_000, limit=200_000).level == "warn"
        assert assess(125_000, 120_000, limit=200_000).level == "urgent"


def test_malformed_threshold_env_falls_back_to_the_default():
    with _Env(CLAW_CONTEXT_WARN="not-a-number"):
        assert assess(140_000, 135_000, limit=200_000).level == "warn"


# --- rendering ------------------------------------------------------------

def test_urgent_text_recommends_a_fresh_session_and_offers_a_handoff():
    out = render_alert(Alert("urgent", Usage(175_000, 200_000), 5_000, 5))
    assert "88%" in out and "175,000" in out
    assert "fresh session" in out
    assert ".clawness/memory.md" in out
    assert out.startswith("--- CLAWNESS CONTEXT ---")
    assert out.rstrip().endswith("--- END CLAWNESS CONTEXT ---")


def test_warn_text_is_brief_and_does_not_derail_the_turn():
    out = render_alert(Alert("warn", Usage(140_000, 200_000), 5_000, 12))
    assert "carry on with their request" in out
    assert len(out) < 500


def test_percent_never_reads_above_one_hundred():
    # Only reachable via an explicit CLAW_CONTEXT_LIMIT set below actual usage.
    u = Usage(182_868, 180_000)
    assert u.percent == 100
    assert "182,868" in render_alert(Alert("urgent", u, 0, 0))


def test_surge_text_names_what_was_added():
    out = render_alert(Alert("surge", Usage(60_000, 200_000), 40_000, 3))
    assert "40,000" in out and "3 turn" in out


# --- session dedup --------------------------------------------------------

def test_each_level_alerts_only_once_per_session():
    sid = "ctx-test-" + os.urandom(6).hex()
    assert should_alert_context(sid, "warn") is True
    assert should_alert_context(sid, "warn") is False


def test_escalation_still_gets_through():
    sid = "ctx-test-" + os.urandom(6).hex()
    assert should_alert_context(sid, "warn") is True
    assert should_alert_context(sid, "urgent") is True


def test_a_lower_level_does_not_re_alert_after_a_higher_one():
    sid = "ctx-test-" + os.urandom(6).hex()
    assert should_alert_context(sid, "urgent") is True
    assert should_alert_context(sid, "warn") is False
    assert should_alert_context(sid, "surge") is False


def test_context_snapshot_returns_the_previous_value():
    sid = "ctx-test-" + os.urandom(6).hex()
    assert context_snapshot(sid, 50_000) == 0
    assert context_snapshot(sid, 90_000) == 50_000


def test_no_session_id_fails_toward_alerting():
    assert should_alert_context("", "warn") is True
    assert context_snapshot("", 1000) == 0


# --- locating the transcript ---------------------------------------------

def test_transcript_path_from_the_payload_is_used():
    p = _transcript(_assistant(0, 0, 1000))
    assert find_transcript({"transcript_path": str(p)}, "/x", "sid") == p


def test_falls_back_to_the_claude_code_layout():
    cfg = Path(tempfile.mkdtemp())
    cwd = "c:\\vscode\\clawness"
    slug = "".join(c if c.isalnum() else "-" for c in cwd)
    proj = cfg / "projects" / slug
    proj.mkdir(parents=True)
    (proj / "abc123.jsonl").write_text(
        json.dumps(_assistant(0, 0, 1000)) + "\n", encoding="utf-8"
    )
    with _Env(CLAUDE_CONFIG_DIR=str(cfg)):
        found = find_transcript({}, cwd, "abc123")
    assert found is not None and found.name == "abc123.jsonl"


def test_no_transcript_anywhere_returns_none():
    with _Env(CLAUDE_CONFIG_DIR=str(Path(tempfile.mkdtemp()))):
        assert find_transcript({}, "/nowhere", "missing-session") is None
        assert find_transcript({}, "/nowhere", "") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all context-watch tests passed")


def test_urgent_text_offers_an_autostart_handoff():
    out = render_alert(Alert("urgent", Usage(tokens=180_000, limit=200_000)))
    assert "**Autostart:** yes" in out and "/clear" in out


# --- the compaction line (1.21) -------------------------------------------

from clawness.context_watch import (  # noqa: E402
    auto_compact_window, compaction_line, idle_floor,
)

_LINE_ENV = dict(
    CLAW_CONTEXT_LIMIT=None, CLAUDE_CODE_DISABLE_1M_CONTEXT=None,
    CLAUDE_CODE_MAX_CONTEXT_TOKENS=None, CLAUDE_CODE_AUTO_COMPACT_WINDOW=None,
    CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=None, CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=None,
    DISABLE_COMPACT=None, DISABLE_AUTO_COMPACT=None,
    CLAW_CONTEXT_WARN=None, CLAW_CONTEXT_URGENT=None,
)


def _line_env(settings: "dict | None" = None, **kw) -> _Env:
    """A clean config dir (no learned cache, no settings) plus *kw*."""
    cfg = Path(tempfile.mkdtemp())
    if settings is not None:
        (cfg / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return _Env(**{**_LINE_ENV, "CLAUDE_CONFIG_DIR": str(cfg), **kw})


def _learn(model: str, pre: int) -> None:
    """Seed the learned-compaction cache under the current CLAUDE_CONFIG_DIR."""
    from clawness import compaction as C
    p = C.cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"v": 1, "files": {},
                             "models": {model: {"samples": [pre]}}}), encoding="utf-8")


def test_default_lines_for_each_window():
    with _line_env():
        assert compaction_line(50_000, window=200_000) == (167_000, "limit")
        # A 1M window compacts at ~967k, but idle compaction caps it at 200k.
        assert compaction_line(50_000, window=1_000_000) == (200_000, "idle")
        assert compaction_line(50_000, model="claude-opus-5-5") == (200_000, "idle")
        # A bigger future window needs no table entry.
        assert compaction_line(50_000, window=2_000_000) == (200_000, "idle")


def test_default_thresholds_land_before_compaction():
    with _line_env():
        assert assess(124_000, 123_000, model="claude-haiku-4-5") is None
        assert assess(125_250, 124_000, model="claude-haiku-4-5").level == "warn"
        assert assess(150_300, 149_000, model="claude-haiku-4-5").level == "urgent"
        assert assess(149_000, 148_000, model="claude-opus-5-5") is None
        assert assess(150_000, 149_000, model="claude-opus-5-5").level == "warn"
        assert assess(180_000, 179_000, model="claude-opus-5-5").level == "urgent"


def test_a_learned_point_lowers_the_line():
    with _line_env():
        _learn("claude-haiku-4-5", 150_000)
        assert compaction_line(50_000, model="claude-haiku-4-5") == (150_000, "limit")


def test_a_learned_point_far_below_the_window_is_ignored():
    """Learned under CLAUDE_CODE_DISABLE_1M_CONTEXT, then the var was removed:
    a 1M model must not stay stuck at 167k forever."""
    with _line_env():
        _learn("claude-opus-5-5", 167_000)
        # 167k is under half the 967k hard point: ignored; idle floor governs.
        assert compaction_line(50_000, model="claude-opus-5-5") == (200_000, "idle")


def test_the_auto_compact_window_env_and_setting():
    with _line_env(CLAUDE_CODE_AUTO_COMPACT_WINDOW="150k"):
        assert auto_compact_window() == 150_000
        assert compaction_line(50_000, model="claude-opus-5-5") == (150_000, "limit")
    with _line_env(settings={"autoCompactWindow": 120_000}):
        assert compaction_line(50_000, model="claude-opus-5-5") == (120_000, "limit")
    # The env var beats the setting, and out-of-range values are ignored.
    with _line_env(settings={"autoCompactWindow": 120_000},
                   CLAUDE_CODE_AUTO_COMPACT_WINDOW="180000"):
        assert auto_compact_window() == 180_000
    with _line_env(CLAUDE_CODE_AUTO_COMPACT_WINDOW="50000"):
        assert auto_compact_window() is None


def test_the_percentage_override_only_lowers():
    with _line_env(CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="50"):
        assert compaction_line(50_000, window=200_000) == (100_000, "limit")
    with _line_env(CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="100"):
        assert compaction_line(50_000, window=200_000) == (167_000, "limit")


def test_the_idle_floor_is_tunable_and_clamped():
    with _line_env(CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS="300000"):
        assert idle_floor() == 300_000
        assert compaction_line(50_000, window=1_000_000) == (300_000, "idle")
    with _line_env(CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS="20000"):
        assert idle_floor() == 100_000          # "values below 100000 are raised"
    with _line_env(CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS="junk"):
        assert idle_floor() == 200_000


def test_compaction_off_falls_back_to_the_window():
    for kw in ({"DISABLE_COMPACT": "1"}, {"DISABLE_AUTO_COMPACT": "1"}):
        with _line_env(**kw):
            assert compaction_line(50_000, window=1_000_000) == (1_000_000, "window")
    with _line_env(settings={"autoCompactEnabled": False}):
        assert compaction_line(50_000, window=200_000) == (200_000, "window")
        assert assess(140_000, 139_000, model="claude-haiku-4-5").level == "warn"


def test_an_explicit_limit_keeps_the_old_percentage_behaviour():
    with _line_env(CLAW_CONTEXT_LIMIT="1000000"):
        assert assess(180_000, 179_000, model="claude-opus-5-5") is None
        assert assess(700_000, 699_000, model="claude-opus-5-5").level == "warn"


def test_haiku_5_5_is_a_native_1m_model():
    with _line_env():
        assert native_window("claude-haiku-5-5") == 1_000_000
        assert native_window("claude-haiku-4-5") is None


def test_the_idle_note_explains_breaks_and_the_distance_left():
    with _line_env():
        out = render_alert(assess(180_000, 179_000, model="claude-opus-5-5"))
    assert "compacts at ~200,000" in out and "~20,000 to go" in out
    assert "break" in out and "fresh session" in out


def test_a_switch_to_a_smaller_model_is_flagged_and_skips_the_surge():
    with _line_env():
        a = assess(160_000, 158_000, model="claude-opus-4-6",
                   previous_model="claude-opus-5-5")
        assert a.level == "urgent" and a.switched and a.added == 0
        assert "model switch lowered" in render_alert(a)
        # Switching UP is not "lowered", even when still urgent.
        b = assess(185_000, 184_000, model="claude-opus-5-5",
                   previous_model="claude-opus-4-6")
        assert b.level == "urgent" and not b.switched


def test_surge_is_measured_against_the_line_not_the_window():
    with _line_env():
        # +60k at 100k on a 1M model: 1M-relative it's 6% (quiet), but the line
        # is 200k, so 30% of it in one turn with ~1 turn left.
        a = assess(100_000, 40_000, model="claude-opus-5-5")
        assert a is not None and a.level == "surge" and a.turns_left == 1


def test_alert_dedup_is_per_model():
    sid = "ctx-test-" + os.urandom(6).hex()
    assert should_alert_context(sid, "urgent", key="claude-opus-5-5") is True
    assert should_alert_context(sid, "warn", key="claude-opus-5-5") is False
    assert should_alert_context(sid, "warn", key="claude-opus-4-6") is True


def test_past_the_line_says_so_instead_of_zero_to_go():
    with _line_env():
        out = render_alert(assess(232_454, 231_000, model="claude-opus-5-5"))
    assert "past the ~200,000 point" in out and "to go" not in out
