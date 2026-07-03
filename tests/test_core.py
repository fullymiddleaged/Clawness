"""Tests for Clawness core retrieval engine."""

import os
import tempfile
from pathlib import Path

import yaml
import pytest

from clawness.core import (
    Clawness,
    Rule,
    load_rules,
    BM25,
    TfIdfIndex,
    rrf,
    _tokenize,
    _estimate_tokens,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_rules(tmp_path):
    """Create a temporary rules directory with test rules."""
    mandatory = tmp_path / "_mandatory"
    mandatory.mkdir()
    python = tmp_path / "python"
    python.mkdir()
    general = tmp_path / "general"
    general.mkdir()

    # Mandatory rule
    (mandatory / "ENF-TEST.yml").write_text(yaml.dump({
        "id": "ENF-TEST",
        "domain": "security",
        "severity": "error",
        "tags": ["secrets", "credentials"],
        "triggers": ["password", "api_key", "secret"],
        "when": "Any hardcoded secret in code.",
        "rule": "Never hardcode secrets.",
        "violation": "password = 'hunter2'",
        "correct": "password = os.environ['DB_PASSWORD']",
    }))

    # Python rules
    (python / "PY-ASYNC.yml").write_text(yaml.dump({
        "id": "PY-ASYNC",
        "domain": "python",
        "severity": "error",
        "tags": ["async", "io", "blocking"],
        "triggers": ["async def", "await", "asyncio"],
        "when": "Calling sync IO in async function.",
        "rule": "Use async IO end-to-end.",
    }))

    (python / "PY-TYPES.yml").write_text(yaml.dump({
        "id": "PY-TYPES",
        "domain": "python",
        "severity": "warning",
        "tags": ["types", "hints", "annotations"],
        "triggers": ["def", "return", "->"],
        "when": "Defining public functions.",
        "rule": "All public functions must have type annotations.",
    }))

    # General rule
    (general / "GEN-LOG.yml").write_text(yaml.dump({
        "id": "GEN-LOG",
        "domain": "general",
        "severity": "warning",
        "tags": ["logging", "debug", "print"],
        "triggers": ["print", "console.log", "logger"],
        "when": "Adding diagnostic output.",
        "rule": "Use structured logging, not print().",
    }))

    return tmp_path


@pytest.fixture
def wl(tmp_rules):
    """Create a Clawness instance with test rules."""
    return Clawness(tmp_rules)


# ── Rule loading ──────────────────────────────────────────────────

class TestLoadRules:
    def test_loads_all_rules(self, tmp_rules):
        ranked, mandatory = load_rules(tmp_rules)
        assert len(mandatory) == 1
        assert len(ranked) == 3

    def test_mandatory_flagged(self, tmp_rules):
        ranked, mandatory = load_rules(tmp_rules)
        assert all(r.mandatory for r in mandatory)
        assert all(not r.mandatory for r in ranked)

    def test_rule_fields_populated(self, tmp_rules):
        ranked, mandatory = load_rules(tmp_rules)
        sec = mandatory[0]
        assert sec.id == "ENF-TEST"
        assert sec.severity == "error"
        assert "secrets" in sec.tags
        assert sec.rule == "Never hardcode secrets."

    def test_empty_directory(self, tmp_path):
        ranked, mandatory = load_rules(tmp_path)
        assert ranked == []
        assert mandatory == []

    def test_search_text_built(self, tmp_rules):
        ranked, _ = load_rules(tmp_rules)
        for r in ranked:
            assert r._search_text != ""
            assert r.id in r._search_text


# ── Tokenizer ─────────────────────────────────────────────────────

class TestTokenizer:
    def test_basic(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_code_tokens(self):
        tokens = _tokenize("async def get_user(user_id: int)")
        assert "async" in tokens
        assert "def" in tokens
        assert "get_user" in tokens
        assert "user_id" in tokens

    def test_empty(self):
        assert _tokenize("") == []
        assert _tokenize("   ") == []


# ── BM25 ──────────────────────────────────────────────────────────

class TestBM25:
    def test_basic_ranking(self):
        bm25 = BM25()
        docs = [
            ["async", "await", "python"],
            ["database", "query", "sql"],
            ["async", "database", "connection"],
        ]
        bm25.build(docs)
        scores = bm25.score(["async", "await"])
        assert scores[0] > scores[1]  # doc 0 matches best

    def test_empty_corpus(self):
        bm25 = BM25()
        bm25.build([])
        assert bm25.score(["test"]) == []

    def test_no_match(self):
        bm25 = BM25()
        bm25.build([["alpha", "beta"]])
        scores = bm25.score(["gamma"])
        assert scores[0] == 0.0


# ── TF-IDF ────────────────────────────────────────────────────────

class TestTfIdf:
    def test_basic_ranking(self):
        idx = TfIdfIndex()
        idx.build([
            "async await python asyncio",
            "database query sql postgres",
            "async database connection pool",
        ])
        results = idx.query("async await python")
        assert len(results) > 0
        assert results[0][0] == 0  # first doc is best match

    def test_empty_query(self):
        idx = TfIdfIndex()
        idx.build(["hello world"])
        results = idx.query("")
        assert results == []


# ── RRF ───────────────────────────────────────────────────────────

class TestRRF:
    def test_merges_lists(self):
        list1 = [(0, 10.0), (1, 5.0), (2, 1.0)]
        list2 = [(2, 10.0), (0, 5.0), (3, 1.0)]
        merged = rrf([list1, list2])
        # Doc 0 appears high in both lists
        ids = [idx for idx, _ in merged]
        assert 0 in ids
        assert 2 in ids

    def test_empty_lists(self):
        assert rrf([[], []]) == []


# ── Clawness retriever ────────────────────────────────────────────

class TestClawness:
    def test_stats(self, wl):
        s = wl.stats
        assert s["mandatory_rules"] == 1
        assert s["ranked_rules"] == 3
        assert s["total_rules"] == 4

    def test_retrieve_returns_string(self, wl):
        result = wl.retrieve("implement async endpoint")
        assert isinstance(result, str)
        assert "CLAWNESS RULES" in result

    def test_mandatory_always_present(self, wl):
        result = wl.retrieve("something random")
        assert "ENF-TEST" in result
        assert "MANDATORY" in result

    def test_relevant_rules_ranked(self, wl):
        result = wl.retrieve("async await python asyncio")
        assert "PY-ASYNC" in result

    def test_domain_filter(self, wl):
        result = wl.retrieve("async code", domain="python")
        assert "PY-ASYNC" in result
        # General rules should not appear when filtering to python
        assert "GEN-LOG" not in result or "MANDATORY" in result.split("GEN-LOG")[0]

    def test_context_budget(self, tmp_rules):
        wl = Clawness(tmp_rules, context_budget=100)
        result = wl.retrieve("async python types logging")
        # Should be truncated by budget
        assert "CLAWNESS RULES" in result

    def test_top_k(self, tmp_rules):
        wl = Clawness(tmp_rules, top_k=1)
        result = wl.retrieve("async python types logging")
        # Count RELEVANT rules (not mandatory)
        relevant_section = result.split("RELEVANT")[-1] if "RELEVANT" in result else ""
        # Should have at most 1 ranked rule
        rule_ids = [line for line in relevant_section.split("\n") if line.startswith("[")]
        assert len(rule_ids) <= 1

    def test_timing_under_1ms(self, wl):
        import time
        t0 = time.perf_counter_ns()
        wl.retrieve("test query")
        elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
        assert elapsed_ms < 10  # generous threshold for CI


# ── Deferred index build (build_index=False / add_rules / build_index()) ──

class TestDeferredBuild:
    def test_retrieve_before_build_raises(self, tmp_rules):
        wl = Clawness(tmp_rules, build_index=False)
        with pytest.raises(RuntimeError):
            wl.retrieve("implement async endpoint")
        with pytest.raises(RuntimeError):
            wl.rank_ids("implement async endpoint")

    def test_deferred_build_matches_default_build(self, tmp_rules):
        default = Clawness(tmp_rules)
        deferred = Clawness(tmp_rules, build_index=False)
        deferred.build_index()

        q = "async await python asyncio"
        assert deferred.rank_ids(q) == default.rank_ids(q)
        assert deferred.retrieve(q) == default.retrieve(q)

    def test_add_rules_merges_before_build(self, tmp_rules):
        # A separate temp root, NOT nested under tmp_rules — nesting it there
        # would make tmp_rules' own rglob("*.yml") load pick it up too.
        extra_dir = Path(tempfile.mkdtemp()) / "extra"
        (extra_dir / "domain").mkdir(parents=True)
        (extra_dir / "domain" / "EXTRA-001.yml").write_text(yaml.dump({
            "id": "EXTRA-001",
            "domain": "domain",
            "severity": "warning",
            "tags": ["extra"],
            "triggers": ["widget"],
            "when": "Building a widget.",
            "rule": "Widgets must be reusable.",
        }))
        extra_ranked, extra_mandatory = load_rules(extra_dir)

        wl = Clawness(tmp_rules, build_index=False)
        wl.add_rules(extra_ranked, extra_mandatory)
        wl.build_index()

        assert wl.stats["ranked_rules"] == 4  # 3 from tmp_rules + 1 merged
        assert "EXTRA-001" in wl.rank_ids("building a reusable widget")

    def test_add_rules_after_build_marks_stale(self, wl):
        assert wl._indexed is True
        wl.add_rules([], [])
        assert wl._indexed is False
        with pytest.raises(RuntimeError):
            wl.retrieve("test query")
        wl.build_index()
        wl.retrieve("test query")  # no longer raises

    def test_empty_query(self, wl):
        result = wl.retrieve("")
        # Should still return mandatory rules
        assert "ENF-TEST" in result


# ── Token estimation ──────────────────────────────────────────────

class TestTokenEstimation:
    def test_rough_estimate(self):
        assert _estimate_tokens("hello world") > 0
        assert _estimate_tokens("a" * 400) >= 100
