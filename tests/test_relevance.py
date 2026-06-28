"""
Tests for the TF-IDF relevance floor that suppresses scattershot ranked rules
on signal-less prompts. The floor must never drop strong matches (eval safety).

Runs under pytest, or standalone:  python tests/test_relevance.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import Clawness  # noqa: E402

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def test_floor_off_returns_full_top_k_for_signal_less_prompt():
    wl = Clawness(RULES_DIR, min_relevance=0.0)
    n = len(wl._rank("hello can you help me", limit=5)[:5])
    assert n == 5  # unfiltered: RRF always fills the slots


def test_floor_suppresses_scattershot_on_signal_less_prompt():
    wl = Clawness(RULES_DIR, min_relevance=0.06)
    n = len(wl._rank("hello can you help me", limit=5)[:5])
    assert n < 5  # the floor trims coincidental matches


def test_floor_keeps_strong_matches():
    """Genuine matches sit far above the noise floor — they must survive."""
    wl = Clawness(RULES_DIR, min_relevance=0.06)
    for query, expected in [
        ("react hooks dependency array", "RCT-HOOKS-001"),
        ("configure cors in fastapi", "FA-CORS-001"),
        ("parameterized sql query to prevent injection", "SQL"),
    ]:
        ids = wl.rank_ids(query, top_k=5)
        assert ids, f"floor wrongly emptied results for {query!r}"
        assert any(e in i for i in ids for e in [expected]), \
            f"{expected} missing from {ids} for {query!r}"


def test_env_var_controls_floor(monkeypatch):
    monkeypatch.setenv("CLAW_MIN_RELEVANCE", "0")
    assert Clawness(RULES_DIR).min_relevance == 0.0
    monkeypatch.setenv("CLAW_MIN_RELEVANCE", "0.2")
    assert Clawness(RULES_DIR).min_relevance == 0.2
    monkeypatch.setenv("CLAW_MIN_RELEVANCE", "garbage")
    assert Clawness(RULES_DIR).min_relevance == 0.06  # falls back to default


if __name__ == "__main__":
    # minimal monkeypatch shim for standalone runs
    import os

    class _MP:
        def setenv(self, k, v):
            os.environ[k] = v

    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(_MP()) if "monkeypatch" in fn.__code__.co_varnames else fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
