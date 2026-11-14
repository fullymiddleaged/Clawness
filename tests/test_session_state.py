"""
Tests for clawness/session_state.py — per-session prompt counting and memory
change tracking behind the session-aware re-injection cadence.

Runs under pytest, or standalone:  python tests/test_session_state.py
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness import session_state as S  # noqa: E402


def _sid() -> str:
    # A fresh id per test so state files never collide across test runs.
    return f"test-{uuid.uuid4()}"


# --- bump_prompt_count -----------------------------------------------------

def test_count_increments_across_calls():
    sid = _sid()
    assert S.bump_prompt_count(sid) == 1
    assert S.bump_prompt_count(sid) == 2
    assert S.bump_prompt_count(sid) == 3


def test_different_sessions_have_independent_counts():
    a, b = _sid(), _sid()
    assert S.bump_prompt_count(a) == 1
    assert S.bump_prompt_count(a) == 2
    assert S.bump_prompt_count(b) == 1  # unaffected by session a


def test_empty_session_id_always_returns_one():
    assert S.bump_prompt_count("") == 1
    assert S.bump_prompt_count("") == 1
    assert S.bump_prompt_count(None) == 1


# --- should_show_full -------------------------------------------------------

def test_full_every_five_cadence():
    full = [c for c in range(1, 17) if S.should_show_full(c, 5)]
    assert full == [1, 6, 11, 16]


def test_full_every_one_always_full():
    assert all(S.should_show_full(c, 1) for c in range(1, 10))
    assert all(S.should_show_full(c, 0) for c in range(1, 10))  # <=1 clamps to always-full


# --- memory_changed ----------------------------------------------------------

def test_memory_changed_true_on_first_check(tmp_path):
    sid = _sid()
    mem = tmp_path / "memory.md"
    mem.write_text("initial", encoding="utf-8")
    assert S.memory_changed(sid, mem) is True


def test_memory_unchanged_on_second_check_without_edit(tmp_path):
    sid = _sid()
    mem = tmp_path / "memory.md"
    mem.write_text("initial", encoding="utf-8")
    assert S.memory_changed(sid, mem) is True
    assert S.memory_changed(sid, mem) is False


def test_memory_changed_again_after_edit(tmp_path):
    sid = _sid()
    mem = tmp_path / "memory.md"
    mem.write_text("initial", encoding="utf-8")
    assert S.memory_changed(sid, mem) is True
    assert S.memory_changed(sid, mem) is False
    time.sleep(0.02)
    mem.write_text("initial\n- new lesson", encoding="utf-8")
    assert S.memory_changed(sid, mem) is True
    assert S.memory_changed(sid, mem) is False  # settles again


def test_memory_changed_true_on_missing_file():
    sid = _sid()
    assert S.memory_changed(sid, "/definitely/does/not/exist.md") is True


def test_memory_changed_true_on_empty_session_id(tmp_path):
    mem = tmp_path / "memory.md"
    mem.write_text("x", encoding="utf-8")
    assert S.memory_changed("", mem) is True
    assert S.memory_changed("", mem) is True  # never settles without a session id


# --- fail-open on a corrupt state file --------------------------------------

def test_corrupt_state_file_fails_toward_full(tmp_path, monkeypatch):
    sid = _sid()
    monkeypatch.setattr(S, "_state_dir", lambda: tmp_path)
    path = S._state_path(sid)
    path.write_text("{ not json", encoding="utf-8")
    assert S.bump_prompt_count(sid) == 1  # corrupt state -> treated as fresh, not a crash


# --- context model tracking (1.21) -------------------------------------------

def test_snapshot_returns_the_previous_tokens_and_model():
    sid = _sid()
    assert S.context_snapshot_with_model(sid, 50_000, "claude-opus-5-5") == (0, None)
    assert S.context_snapshot_with_model(sid, 60_000, "claude-opus-4-6") == (
        50_000, "claude-opus-5-5")
    # No model on this read: the last known one is kept, not erased.
    assert S.context_snapshot_with_model(sid, 61_000) == (60_000, "claude-opus-4-6")
    assert S.context_snapshot_with_model(sid, 62_000)[1] == "claude-opus-4-6"


def test_a_model_switch_rearms_the_alert_levels():
    sid = _sid()
    assert S.should_alert_context(sid, "urgent", key="claude-opus-5-5") is True
    assert S.should_alert_context(sid, "urgent", key="claude-opus-5-5") is False
    assert S.should_alert_context(sid, "urgent", key="claude-opus-4-6") is True
    # Switching back doesn't repeat what that model already said.
    assert S.should_alert_context(sid, "warn", key="claude-opus-5-5") is False


def test_a_pre_1_21_state_file_still_dedups():
    """Old state kept one string. It reads as the no-model record, so an
    upgrade mid-session doesn't repeat an alert the session already got."""
    sid = _sid()
    S._save(sid, {"context_alert": "warn"})
    assert S.should_alert_context(sid, "warn") is False
    assert S.should_alert_context(sid, "urgent") is True
    assert "context_alert" not in S._load(sid)


# --- compaction (1.21) -------------------------------------------------------

def test_a_halved_context_rearms_the_alert_levels():
    """A compacted session keeps its id, so without a reset the urgent alert it
    already got would stay 'said' and the refilled session would never be
    warned again."""
    sid = _sid()
    S.context_snapshot_with_model(sid, 185_000, "claude-opus-5-5")
    assert S.should_alert_context(sid, "urgent", key="claude-opus-5-5") is True
    S.context_snapshot_with_model(sid, 40_000, "claude-opus-5-5")
    assert S.should_alert_context(sid, "urgent", key="claude-opus-5-5") is True


def test_ordinary_growth_and_small_dips_keep_the_dedup():
    sid = _sid()
    S.context_snapshot_with_model(sid, 150_000, "m")
    assert S.should_alert_context(sid, "warn", key="m") is True
    S.context_snapshot_with_model(sid, 120_000, "m")  # a dip, not a compaction
    S.context_snapshot_with_model(sid, 160_000, "m")
    assert S.should_alert_context(sid, "warn", key="m") is False


def test_mark_compacted_forces_one_full_render_and_rearms(tmp_path):
    sid = _sid()
    mem = tmp_path / "memory.md"
    mem.write_text("x", encoding="utf-8")
    S.memory_changed(sid, mem)
    assert S.memory_changed(sid, mem) is False
    assert S.should_alert_context(sid, "urgent", key="m") is True

    S.mark_compacted(sid)
    assert S.take_compacted(sid) is True
    assert S.take_compacted(sid) is False  # one-shot
    assert S.memory_changed(sid, mem) is True  # newest lessons show again
    assert S.should_alert_context(sid, "urgent", key="m") is True


def test_take_compacted_is_false_without_a_compaction():
    assert S.take_compacted(_sid()) is False
    assert S.take_compacted("") is False
    S.mark_compacted("")  # no session id: a no-op, not a crash


if __name__ == "__main__":
    import tempfile
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        code = fn.__code__
        if "tmp_path" in code.co_varnames[:code.co_argcount]:
            with tempfile.TemporaryDirectory() as d:
                if "monkeypatch" in code.co_varnames[:code.co_argcount]:
                    continue  # skip monkeypatch-only test in standalone mode
                fn(Path(d))
        else:
            fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
