"""
Tests for the TF-IDF relevance floor that suppresses scattershot ranked rules
on signal-less prompts. The floor must never drop strong matches (eval safety).

Runs under pytest, or standalone:  python tests/test_relevance.py
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import Clawness  # noqa: E402

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def _write_rule(root: Path, domain: str, rule_id: str, rule_text: str, triggers=("t",)) -> None:
    path = root / domain / f"{rule_id}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(f"""\
        id: {rule_id}
        domain: {domain}
        severity: warning
        tags: [t]
        triggers: {list(triggers)}
        when: When something happens.
        rule: {rule_text}
        """), encoding="utf-8")


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
    # show_meta=True: scores are hidden by default (per-turn churn defeats
    # prompt caching) — this test checks their SEMANTICS when shown.
    block = wl.retrieve("what rules do you see", show_meta=True)
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


def test_bm25_rescue_surfaces_a_rare_term_match_tfidf_alone_would_drop(tmp_path):
    """A rule can rank #1 on BM25 (a rare, high-IDF trigger term) while its TF-IDF
    cosine sits below the floor — a long document dilutes cosine similarity even
    for a term that appears nowhere else in the corpus. Before the rescue, the
    floor (gauged on TF-IDF alone) dropped it despite BM25's confident #1 rank,
    silently defeating half the RRF fusion. Reproduced with a synthetic corpus
    since it doesn't occur naturally in the real (short-document) rule corpus."""
    filler = (
        "common word filler text about generic programming practices software "
        "engineering conventions style guidelines architecture patterns "
        "maintainability readability documentation "
    )
    _write_rule(tmp_path, "general", "GEN-RARE-001", "zzzrareterm " + filler * 15)
    for i in range(20):
        _write_rule(tmp_path, "general", f"GEN-DECOY-{i:03d}",
                    f"decoy rule number {i} about topic {i} unrelated database css docker python testing")

    wl = Clawness(tmp_path, min_relevance=0.06)

    # Confirm the setup actually reproduces the gap this test targets.
    tfidf_map = dict(wl._tfidf.query("zzzrareterm", top_k=25))
    rare_idx = next(i for i, r in enumerate(wl._ranked_rules) if r.id == "GEN-RARE-001")
    assert tfidf_map.get(rare_idx, 0.0) < 0.06, "test setup no longer reproduces a sub-floor TF-IDF cosine"

    ids = wl.rank_ids("zzzrareterm", top_k=5)
    assert ids == ["GEN-RARE-001"], f"BM25's confident top hit was dropped at the floor: {ids}"


def test_bm25_rescue_never_fires_when_tfidf_already_cleared_the_floor():
    """Rescue only fires on an otherwise-empty result — a query that already
    surfaces matches via TF-IDF (including a signal-less/noise prompt, which
    empirically still returns something) is completely unaffected by it."""
    wl = Clawness(RULES_DIR, min_relevance=0.06)
    ids = wl.rank_ids("hello can you help me", top_k=5)
    # The floor already trims this down from a full top-5 (test_floor_suppresses_
    # scattershot_on_signal_less_prompt covers the same query via `_rank`
    # directly) — confirm it's non-empty (so the rescue path is never reached)
    # and still bounded (not full top-5).
    assert 0 < len(ids) < 5


def test_off_stack_rules_suppressed_when_stack_known():
    """A vague prompt in a Python project should not surface off-stack
    (React/CSS/SQL/etc.) language rules — only in-stack + cross-cutting ones.

    Probes several signal-less prompts rather than one. The original single
    probe was "what rules do you see", which is NOT signal-less against
    RCT-HOOKS-001 — that rule is literally about the "Rules of Hooks", so the
    shared token "rules" gave it a real lexical match that sat a hair under the
    0.15 off-stack floor (0.137). Corpus growth raised the IDF of "rules" and
    pushed it to 0.151, failing a test whose property still held. Pick prompts
    with no token in common with any rule, and probe more than one, so the test
    measures suppression instead of one rule's knife-edge score."""
    off_stack = {"react", "css", "sql", "capacitor", "go", "rust", "java",
                 "nextjs", "docker", "fastapi", "typescript", "llm"}
    wl = Clawness(RULES_DIR, stack_domains={"python", "bash", "general", "workflows"})
    by_id = {r.id: r for r in wl._ranked_rules}
    for prompt in ("hello", "ok thanks", "continue", "what should i do next"):
        ids = wl.rank_ids(prompt, top_k=8)
        leaked = {by_id[i].domain for i in ids} & off_stack
        assert not leaked, f"off-stack domains leaked through on {prompt!r}: {leaked}"


def test_llm_domain_is_stack_gated():
    """llm/ is a stack domain, so prompt-caching and eval-set rules stay quiet in
    a project that calls no model, while a project with an LLM SDK gets them.
    Uses a marginal match on purpose: a STRONG match is designed to clear the
    off-stack floor anyway (test_strong_off_stack_match_still_surfaces), so only
    a mid-band score exercises the gate."""
    prompt = "confirm before the side effect"
    off = Clawness(RULES_DIR, stack_domains={"python", "science", "general"})
    on = Clawness(RULES_DIR, stack_domains={"python", "llm", "general"})
    off_ids = [i for i in off.rank_ids(prompt, top_k=8) if i.startswith("LLM-")]
    on_ids = [i for i in on.rank_ids(prompt, top_k=8) if i.startswith("LLM-")]
    assert not off_ids, f"llm rules leaked into a non-LLM project: {off_ids}"
    assert on_ids, "llm rules missing from a project that uses an LLM SDK"


def test_science_domain_is_cross_cutting():
    """science/ must NOT be stack-gated: a researcher often works in a bare or
    LaTeX-only directory where nothing is detected, and gating would silence the
    rules exactly there. A physics question must win over the CSS units rule."""
    wl = Clawness(RULES_DIR, stack_domains={"rust", "general"})
    ids = wl.rank_ids("check the units in this equation", top_k=5)
    assert "SCI-UNITS-001" in ids
    assert ids[0] == "SCI-UNITS-001", f"CSS/other rule outranked it: {ids[:3]}"


# Ordinary development prompts — none is about science or research. Kept as a
# batch because the failure mode is statistical: any single prompt pulling one
# science rule looks like bad luck, and only the rate shows the real problem.
_ROUTINE_DEV_PROMPTS = [
    "extract this into a helper function", "my component re-renders too often",
    "add pagination to this endpoint", "why is my docker image so big",
    "handle a form submission", "rename this variable", "fix this type error",
    "add error handling to this function", "write a test for this",
    "the build is failing", "refactor this class", "add logging here",
    "why is this query slow", "set up ci for this repo", "update the readme",
    "add a new api route", "validate this request body", "fix the lint errors",
    "cache the response", "add a database migration", "deploy this to prod",
    "review this pull request", "split this file up", "add types to this module",
    "why does this return undefined", "clean up these imports",
    "make this function async", "handle the null case", "add a loading state",
    "fix the failing test",
]


def test_science_research_do_not_leak_into_routine_dev_work():
    """science/ and research/ must stay out of ordinary coding results.

    They are cross-cutting (never stack-gated) so a researcher in a bare
    directory still gets them — which means only the topical floor and trigger
    precision hold them back. At 1.3.0, 11 of these 30 prompts surfaced one
    ("write a test for this" -> SCI-PAPER-001 via the word "tested" in its body,
    "the build is failing" -> RES-NOVELTY-001 via "Failing to find"). The stack
    filter makes it worse, not better: suppressing off-stack rules frees top-k
    slots that these then fill, so this asserts under a real stack."""
    for stack in ({"typescript", "react", "nextjs", "css", "general"},
                  {"python", "fastapi", "sql", "general"}):
        wl = Clawness(RULES_DIR, stack_domains=stack)
        leaks = []
        for prompt in _ROUTINE_DEV_PROMPTS:
            for rid in wl.rank_ids(prompt, top_k=5):
                if rid.startswith(("SCI-", "RES-")):
                    leaks.append((prompt, rid))
        assert not leaks, f"science/research leaked into routine dev work: {leaks}"


def test_topical_floor_does_not_silence_real_research_questions():
    """The topical floor must not cost recall: a genuine science or research
    question scores far above it, including in a directory where nothing is
    detected (the bare-LaTeX case that made these domains cross-cutting)."""
    wl = Clawness(RULES_DIR, stack_domains={"general"})
    for prompt, expected in [
        ("check the units in this equation", "SCI-UNITS-001"),
        ("verify this derivation", "SCI-DERIVE-001"),
        ("is this p value significant", "SCI-STATS-001"),
        ("is this idea actually novel", "RES-NOVELTY-001"),
        ("where should i start investigating this area", "RES-QUESTION-001"),
        ("write the abstract for my paper", "SCI-PAPER-001"),
    ]:
        ids = wl.rank_ids(prompt, top_k=5)
        assert expected in ids, f"{prompt!r} lost {expected}; got {ids}"


def test_topical_floor_is_between_base_and_off_stack():
    """Ordering matters: at or below the base floor it does nothing, and at the
    off-stack floor it would effectively gate domains we chose not to gate."""
    wl = Clawness(RULES_DIR)
    assert wl.min_relevance < wl.topical_min_relevance <= wl.off_stack_min_relevance
    assert wl._floor_for("science") == wl.topical_min_relevance
    assert wl._floor_for("research") == wl.topical_min_relevance
    assert wl._floor_for("general") == wl.min_relevance
    assert wl._floor_for("meta") == wl.min_relevance


def test_narrow_floor_sits_above_the_ordinary_off_stack_floor():
    """cfd/julia/fortran/matlab/r are in _STACK_DOMAINS too, so the narrow tier has
    to be tested BEFORE the ordinary off-stack return or it's dead code."""
    wl = Clawness(RULES_DIR, stack_domains={"python", "general"})
    assert wl.off_stack_min_relevance < wl.narrow_min_relevance
    for domain in ("cfd", "julia", "fortran", "matlab", "r"):
        assert wl._floor_for(domain) == wl.narrow_min_relevance, domain
    # A merely off-stack domain keeps the ordinary floor...
    assert wl._floor_for("react") == wl.off_stack_min_relevance
    # ...and in its OWN project a narrow domain is not penalised at all.
    own = Clawness(RULES_DIR, stack_domains={"julia", "cfd", "general"})
    assert own._floor_for("julia") == own.min_relevance
    assert own._floor_for("cfd") == own.min_relevance


def test_colliding_vocabulary_does_not_drag_narrow_domains_into_dev_work():
    """The reason the narrow tier exists. Every one of these is ordinary dev
    language that also happens to be CFD/MATLAB/R/Fortran vocabulary — at the
    0.15 off-stack floor they surfaced (CFD-CONVERGE-001 scored 0.190 on the
    first one, MATLAB and R 0.193/0.163 on the second)."""
    wl = Clawness(RULES_DIR, stack_domains={"python", "bash", "general", "workflows"})
    by_id = {r.id: r for r in wl._ranked_rules}
    narrow = {"cfd", "julia", "fortran", "matlab", "r"}
    for prompt in (
        "the solver is not converging, fix the residual bug",
        "vectorize this dataframe loop",
        "the build is failing",
        "write a test for this function",
        "why is this function so slow",
    ):
        leaked = {by_id[i].domain for i in wl.rank_ids(prompt, top_k=5)} & narrow
        assert not leaked, f"{prompt!r} leaked {leaked}"


def test_an_explicit_narrow_ask_still_gets_through_off_stack():
    """The floor is high, not a gate: someone in a Python repo who explicitly asks
    a CFD or Julia question still gets the rule."""
    wl = Clawness(RULES_DIR, stack_domains={"python", "general"})
    assert "CFD-TURB-001" in wl.rank_ids(
        "which turbulence model for this openfoam case", top_k=5)
    assert "JL-TYPE-001" in wl.rank_ids(
        "fix the type instability in my julia function", top_k=5)


def test_narrow_floor_env_var_and_never_below_off_stack(monkeypatch):
    monkeypatch.setenv("CLAW_NARROW_MIN_RELEVANCE", "0.4")
    assert Clawness(RULES_DIR).narrow_min_relevance == 0.4
    wl = Clawness(RULES_DIR, off_stack_min_relevance=0.3, narrow_min_relevance=0.1)
    assert wl.narrow_min_relevance == 0.3


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
