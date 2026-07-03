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
