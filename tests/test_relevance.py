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


def test_rendered_score_is_relevance_above_floor():
    """The displayed number must be the TF-IDF relevance (comparable to the floor),
    not the rank-based RRF score (~0.03, which read as 'below floor' and misled)."""
    import re
    wl = Clawness(RULES_DIR, min_relevance=0.06)
    block = wl.retrieve("what rules do you see")
    assert "relevance=" in block
    assert "score=" not in block
    # every shown relevance must be >= the floor
    for val in re.findall(r"relevance=([0-9.]+)", block):
        assert float(val) >= 0.06, f"shown relevance {val} is below the floor"


def test_rank_returns_tfidf_relevance_not_rrf():
    wl = Clawness(RULES_DIR, min_relevance=0.0)
    q = "configure cors in fastapi"
    tfidf = dict(wl._tfidf.query(q, top_k=10))
    for idx, relevance in wl._rank(q, limit=5)[:5]:
        assert abs(relevance - tfidf.get(idx, 0.0)) < 1e-9  # carries the cosine, not RRF


def test_off_stack_rules_suppressed_when_stack_known():
    """A vague prompt in a Python project should not surface off-stack
    (React/CSS/SQL/etc.) language rules — only in-stack + cross-cutting ones."""
    wl = Clawness(RULES_DIR, stack_domains={"python", "bash", "general", "workflows"})
    ids = wl.rank_ids("what rules do you see", top_k=8)
    domains = {wl._ranked_rules[i].domain
               for i in range(len(wl._ranked_rules))
               if wl._ranked_rules[i].id in ids}
    off_stack = {"react", "css", "sql", "capacitor", "go", "rust", "java",
                 "nextjs", "docker", "fastapi", "typescript"}
    assert not (domains & off_stack), f"off-stack domains leaked through: {domains & off_stack}"


def test_strong_off_stack_match_still_surfaces():
    """A genuinely strong cross-domain match must clear the off-stack floor, so a
    React question in a Python repo still gets React rules (mid-session deps)."""
    wl = Clawness(RULES_DIR, stack_domains={"python", "general"})
    ids = wl.rank_ids("fix my react useEffect hook dependency array", top_k=5)
    assert "RCT-HOOKS-001" in ids


def test_cross_cutting_rules_never_penalized():
    """general/meta/workflows rules apply regardless of stack — base floor only."""
    wl = Clawness(RULES_DIR, stack_domains={"python"})
    assert wl._floor_for("general") == wl.min_relevance
    assert wl._floor_for("meta") == wl.min_relevance
    assert wl._floor_for("workflows") == wl.min_relevance
    # an off-stack language domain gets the higher floor
    assert wl._floor_for("react") == wl.off_stack_min_relevance
    # an in-stack language domain gets the base floor
    assert wl._floor_for("python") == wl.min_relevance


def test_no_stack_means_no_penalty():
    """stack_domains=None (CLI/eval default) leaves retrieval unchanged."""
    wl = Clawness(RULES_DIR, stack_domains=None)
    assert wl._floor_for("react") == wl.min_relevance
    assert wl._floor_for("sql") == wl.min_relevance


def test_off_stack_floor_env_var_and_never_below_base(monkeypatch):
    monkeypatch.setenv("CLAW_OFFSTACK_MIN_RELEVANCE", "0.25")
    assert Clawness(RULES_DIR).off_stack_min_relevance == 0.25
    # off-stack floor can't drop below the base floor
    wl = Clawness(RULES_DIR, min_relevance=0.3, off_stack_min_relevance=0.1)
    assert wl.off_stack_min_relevance == 0.3


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
