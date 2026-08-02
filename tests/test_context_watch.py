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
    find_transcript,
    infer_limit,
    limit_from_settings,
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
