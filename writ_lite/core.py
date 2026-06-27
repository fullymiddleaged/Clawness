"""
Clawness — lightweight hybrid rule retrieval for AI coding agents.

Keeps the ideas from infinri/Writ (hybrid ranking, mandatory rules, context
budgets) but drops Neo4j, ONNX, Docker, and the FastAPI daemon. The entire
retriever runs in-process in pure Python.

Dependencies: pyyaml (usually preinstalled). Nothing else.

Retrieval pipeline:
  1. Load & parse YAML rule files from a rules directory tree
  2. Mandatory rules (in _mandatory/) are set aside — always returned
  3. Tokenizer adds light stems + concept markers (auth/jwt -> __auth__)
     so queries match rules that use different words for the same idea
  4. BM25-Okapi keyword search over rule text (pure Python)
  5. TF-IDF cosine similarity over rule text (pure Python)
  6. Optional model2vec static-embedding ranker (if installed) for true
     vector semantics — see embeddings.py; falls back silently if absent
  7. Reciprocal Rank Fusion merges the ranked lists
  8. Context budget caps total output tokens

Typical corpus (<500 rules): ~1 ms end-to-end lexical, <1 MB on disk.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    domain: str
    severity: str = "warning"            # error | warning | info
    mandatory: bool = False
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    when: str = ""
    rule: str = ""
    violation: str = ""
    correct: str = ""
    source_path: str = ""

    # ---- derived fields (populated at index time) ----
    _search_text: str = field(default="", repr=False)

    def build_search_text(self) -> str:
        """Concatenate all searchable fields into one string for indexing."""
        parts = [
            self.id,
            self.domain,
            " ".join(str(t) for t in self.tags if t),
            " ".join(str(t) for t in self.triggers if t),
            self.when,
            self.rule,
            self.violation,
            self.correct,
        ]
        self._search_text = " ".join(p for p in parts if p)
        return self._search_text

    def render(self, score: float | None = None) -> str:
        """Format for injection into agent context."""
        score_str = f" score={score:.3f}" if score is not None else ""
        lines = [f"[{self.id}] ({self.domain}/{self.severity}){score_str}"]
        if self.when:
            lines.append(f"  WHEN: {self.when}")
        if self.rule:
            lines.append(f"  RULE: {self.rule}")
        if self.violation:
            lines.append(f"  BAD:  {self.violation}")
        if self.correct:
            lines.append(f"  GOOD: {self.correct}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rule loader
# ---------------------------------------------------------------------------

def load_rules(rules_dir: str | Path) -> tuple[list[Rule], list[Rule]]:
    """
    Walk *rules_dir* and return (ranked_rules, mandatory_rules).

    Any rule file under a directory named '_mandatory' is treated as
    mandatory (always injected, never ranked).
    """
    rules_dir = Path(rules_dir)
    ranked: list[Rule] = []
    mandatory: list[Rule] = []

    for yml_path in sorted(rules_dir.rglob("*.yml")):
        with open(yml_path) as f:
            data = yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            continue

        is_mandatory = "_mandatory" in yml_path.parts

        r = Rule(
            id=str(data.get("id", yml_path.stem)),
            domain=str(data.get("domain", yml_path.parent.name)),
            severity=str(data.get("severity", "warning")),
            mandatory=is_mandatory,
            tags=[str(t) for t in (data.get("tags") or []) if t is not None],
            triggers=[str(t) for t in (data.get("triggers") or []) if t is not None],
            when=str(data.get("when") or ""),
            rule=str(data.get("rule") or ""),
            violation=str(data.get("violation") or ""),
            correct=str(data.get("correct") or ""),
            source_path=str(yml_path),
        )
        r.build_search_text()

        if is_mandatory:
            mandatory.append(r)
        else:
            ranked.append(r)

    return ranked, mandatory


# ---------------------------------------------------------------------------
# Tokenizer (shared by BM25 and TF-IDF)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tokenizer (shared by BM25 and TF-IDF)
#
# Two zero-dependency upgrades over plain word-splitting give the lexical
# rankers a semantic-ish reach without any model:
#   1. Light stemming collapses morphological variants (maintained ->
#      maintain, libraries -> library) so a query word matches a rule word
#      even when the surface form differs.
#   2. Concept expansion maps domain synonyms onto a shared marker token
#      (auth/jwt/oauth/login/session -> __auth__), applied symmetrically to
#      both rules and queries, so "handle login tokens" can match a rule
#      written about "authentication". For true vector semantics, install
#      model2vec and the embedding ranker activates automatically (see
#      embeddings.py); this layer is the always-on floor beneath it.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# Maps a surface term to one or more shared concept markers. Applied to both
# documents and queries, so any two terms sharing a concept become matchable.
_CONCEPT_GROUPS: dict[str, tuple[str, ...]] = {
    "__auth__": (
        "auth", "authentication", "authorization", "authorize", "authn",
        "authz", "login", "logout", "signin", "signout", "signup", "session",
        "sso", "oauth", "oauth2", "jwt", "token", "credential", "credentials",
        "password", "passwords", "passwd", "permission", "permissions", "rbac",
    ),
    "__db__": (
        "db", "database", "databases", "sql", "query", "queries", "postgres",
        "postgresql", "mysql", "sqlite", "mariadb", "mongo", "mongodb", "orm",
        "table", "tables", "schema", "migration", "migrations", "index",
        "transaction", "transactions", "join", "joins",
    ),
    "__async__": (
        "async", "asynchronous", "await", "promise", "promises", "future",
        "futures", "coroutine", "coroutines", "concurrency", "concurrent",
        "parallel", "thread", "threads", "threading", "goroutine", "goroutines",
        "nonblocking",
    ),
    "__error__": (
        "error", "errors", "exception", "exceptions", "panic", "fail",
        "failure", "failures", "crash", "throw", "throws", "raise", "raises",
        "catch", "rescue", "fault",
    ),
    "__test__": (
        "test", "tests", "testing", "unittest", "pytest", "jest", "vitest",
        "spec", "specs", "tdd", "coverage", "mock", "mocks", "stub", "fixture",
        "assertion", "assert",
    ),
    "__security__": (
        "security", "secure", "vulnerability", "vulnerabilities", "vuln",
        "xss", "csrf", "injection", "sanitize", "sanitization", "exploit",
        "exploits", "harden", "hardening", "owasp",
    ),
    "__perf__": (
        "performance", "perf", "optimize", "optimization", "optimise", "latency",
        "throughput", "speed", "slow", "fast", "cache", "caching", "memoize",
        "bottleneck", "profiling",
    ),
    "__log__": (
        "log", "logs", "logging", "logger", "observability", "telemetry",
        "trace", "tracing", "metrics", "monitoring", "audit",
    ),
    "__config__": (
        "config", "configuration", "env", "environment", "settings", "dotenv",
        "secrets", "secret",
    ),
    "__dependency__": (
        "dependency", "dependencies", "package", "packages", "library",
        "libraries", "module", "modules", "import", "imports", "npm", "pip",
        "cargo", "maven", "vendor", "vendored", "maintained", "maintainer",
        "maintenance", "lockfile", "semver",
    ),
    "__type__": (
        "type", "types", "typing", "typed", "typescript", "annotation",
        "annotations", "generic", "generics", "interface", "interfaces",
    ),
    "__memory__": (
        "memory", "leak", "leaks", "allocation", "alloc", "gc", "garbage",
        "buffer", "buffers", "heap", "stack", "oom",
    ),
    "__ui__": (
        "ui", "frontend", "css", "style", "styles", "styling", "layout",
        "responsive", "component", "components", "render", "rendering",
        "accessibility", "a11y",
    ),
    "__api__": (
        "api", "endpoint", "endpoints", "rest", "restful", "graphql", "route",
        "routes", "routing", "controller", "handler", "handlers", "request",
        "requests", "response", "responses", "http",
    ),
    "__validation__": (
        "validate", "validation", "validator", "sanitize", "schema", "zod",
        "pydantic", "constraint", "constraints",
    ),
    "__container__": (
        "docker", "dockerfile", "container", "containers", "image", "images",
        "kubernetes", "k8s", "compose", "pod", "pods",
    ),
}

# Invert to term -> (marker, ...). A term may belong to several concepts.
_CONCEPTS: dict[str, tuple[str, ...]] = {}
for _marker, _terms in _CONCEPT_GROUPS.items():
    for _t in _terms:
        _CONCEPTS[_t] = _CONCEPTS.get(_t, ()) + (_marker,)

_STEM_RULES: tuple[tuple[str, str], ...] = (
    ("ies", "y"),   # libraries -> library, dependencies -> dependency
    ("ing", ""),    # caching -> cach, logging -> logg (symmetric, still matches)
    ("ed", ""),     # maintained -> maintain
    ("es", ""),     # caches -> cach
    ("s", ""),      # tokens -> token
)


def _stem(tok: str) -> str:
    """Very light suffix stripper. Conservative: only touches tokens long
    enough that stripping leaves a real stem, so it collapses common
    plural/verb forms without mangling short identifiers."""
    if len(tok) <= 4:
        return tok
    for suf, repl in _STEM_RULES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)] + repl
    return tok


def _tokenize(text: str) -> list[str]:
    """Tokenize, then augment with stems and concept markers. The original
    token is always kept (so exact matches retain full weight); stems and
    concept markers are added on top to widen recall."""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        out.append(tok)
        stem = _stem(tok)
        if stem != tok:
            out.append(stem)
        concepts = _CONCEPTS.get(tok) or _CONCEPTS.get(stem)
        if concepts:
            out.extend(concepts)
    return out



# ---------------------------------------------------------------------------
# BM25-Okapi (pure Python, no dependencies)
# ---------------------------------------------------------------------------

class BM25:
    """
    BM25-Okapi ranking. Pure Python implementation.

    Parameters match the standard defaults (k1=1.5, b=0.75).
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._corpus_size = 0
        self._avgdl = 0.0
        self._doc_len: list[int] = []
        self._doc_freqs: dict[str, int] = {}       # term -> num docs containing it
        self._term_freqs: list[dict[str, int]] = [] # per-doc term counts
        self._idf: dict[str, float] = {}

    def build(self, documents: list[list[str]]) -> None:
        """Build index from pre-tokenized documents."""
        self._corpus_size = len(documents)
        self._doc_len = [len(d) for d in documents]
        self._avgdl = sum(self._doc_len) / max(self._corpus_size, 1)

        self._doc_freqs = {}
        self._term_freqs = []

        for doc in documents:
            tf = {}
            seen = set()
            for token in doc:
                tf[token] = tf.get(token, 0) + 1
                if token not in seen:
                    self._doc_freqs[token] = self._doc_freqs.get(token, 0) + 1
                    seen.add(token)
            self._term_freqs.append(tf)

        # pre-compute IDF
        self._idf = {}
        n = self._corpus_size
        for term, df in self._doc_freqs.items():
            self._idf[term] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query_tokens: list[str]) -> list[float]:
        """Return BM25 scores for all documents given a tokenized query."""
        scores = [0.0] * self._corpus_size
        for q in query_tokens:
            idf = self._idf.get(q, 0.0)
            if idf <= 0:
                continue
            for i in range(self._corpus_size):
                tf = self._term_freqs[i].get(q, 0)
                if tf == 0:
                    continue
                dl = self._doc_len[i]
                num = tf * (self.k1 + 1)
                den = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                scores[i] += idf * num / den
        return scores


# ---------------------------------------------------------------------------
# TF-IDF (pure Python, no numpy/sklearn)
# ---------------------------------------------------------------------------

class TfIdfIndex:
    """Sparse TF-IDF index with cosine similarity. Zero dependencies."""

    def __init__(self) -> None:
        self._doc_freqs: Counter = Counter()
        self._doc_vectors: list[dict[str, float]] = []
        self._n_docs: int = 0

    def _idf(self, term: str) -> float:
        """Smoothed IDF. The +1 keeps weights non-negative even for terms
        that appear in more than half the corpus (an unsmoothed
        log(n / (1+df)) goes negative there and can zero out real matches)."""
        df = self._doc_freqs.get(term, 0)
        return math.log(self._n_docs / (1 + df)) + 1.0

    def build(self, documents: list[str]) -> None:
        self._n_docs = len(documents)
        tokenized = [_tokenize(doc) for doc in documents]

        self._doc_freqs = Counter()
        for tokens in tokenized:
            for term in set(tokens):
                self._doc_freqs[term] += 1

        self._doc_vectors = []
        for tokens in tokenized:
            tf = Counter(tokens)
            vec: dict[str, float] = {}
            for term, count in tf.items():
                tf_score = 1.0 + math.log(count) if count > 0 else 0.0
                vec[term] = tf_score * self._idf(term)
            self._doc_vectors.append(vec)

    def query(
        self,
        text: str,
        top_k: int = 20,
        candidates: Optional[set[int]] = None,
    ) -> list[tuple[int, float]]:
        """Return [(doc_index, cosine_score), ...] sorted descending.

        If *candidates* is given, only those document indices are scored.
        Filtering happens before truncation so an in-domain match is never
        crowded out of the top_k by documents that will be discarded anyway.
        """
        tokens = _tokenize(text)
        tf = Counter(tokens)
        q_vec: dict[str, float] = {}
        for term, count in tf.items():
            tf_score = 1.0 + math.log(count) if count > 0 else 0.0
            q_vec[term] = tf_score * self._idf(term)

        scores: list[tuple[int, float]] = []
        for i, d_vec in enumerate(self._doc_vectors):
            if candidates is not None and i not in candidates:
                continue
            sim = _cosine(q_vec, d_vec)
            if sim > 0:
                scores.append((i, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    common = a.keys() & b.keys()
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def rrf(
    ranked_lists: list[list[tuple[int, float]]], k: int = 60
) -> list[tuple[int, float]]:
    """Merge multiple ranked lists via RRF. Returns [(index, fused_score)]."""
    scores: dict[int, float] = {}
    for rlist in ranked_lists:
        for rank, (idx, _raw) in enumerate(rlist):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Main retriever
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4 + 1


class WritLite:
    """
    Lightweight hybrid retriever.

    Usage:
        wl = WritLite("/path/to/rules")
        block = wl.retrieve("implement async endpoint for user creation")
        print(block)
    """

    def __init__(
        self,
        rules_dir: str | Path,
        context_budget: int = 4000,     # max tokens for rule block
        top_k: int = 8,                 # max ranked rules to return
        embedder: object = "auto",      # "auto" | None | a Model2VecEmbedder
    ) -> None:
        self.rules_dir = Path(rules_dir)
        self.context_budget = context_budget
        self.top_k = top_k

        # optional semantic embedder (None = lexical only)
        if embedder == "auto":
            from .embeddings import get_default_embedder
            self._embedder = get_default_embedder()
        else:
            self._embedder = embedder
        self._rule_vecs = None

        # load
        self._ranked_rules, self._mandatory_rules = load_rules(self.rules_dir)

        if not self._ranked_rules:
            self._bm25 = None
            self._tfidf = None
            return

        # build search texts
        search_texts = [r._search_text for r in self._ranked_rules]
        tokenized = [_tokenize(t) for t in search_texts]

        # BM25 index
        self._bm25 = BM25()
        self._bm25.build(tokenized)

        # TF-IDF index
        self._tfidf = TfIdfIndex()
        self._tfidf.build(search_texts)

        # Optional embedding matrix (cached on disk). Best-effort: any failure
        # disables the semantic ranker rather than breaking retrieval.
        if self._embedder is not None:
            try:
                from .embeddings import build_rule_matrix
                self._rule_vecs = build_rule_matrix(self._embedder, search_texts)
            except Exception:
                self._embedder = None
                self._rule_vecs = None

    @property
    def stats(self) -> dict:
        emb = None
        if getattr(self, "_embedder", None) is not None and self._rule_vecs is not None:
            emb = getattr(self._embedder, "name", "enabled")
        return {
            "ranked_rules": len(self._ranked_rules),
            "mandatory_rules": len(self._mandatory_rules),
            "total_rules": len(self._ranked_rules) + len(self._mandatory_rules),
            "rules_dir": str(self.rules_dir),
            "embeddings": emb,
        }

    def _embed_rank(
        self, query: str, candidate_indices: list[int], limit: int
    ) -> list[tuple[int, float]]:
        """Cosine-rank candidate rules against the query embedding. Returns
        [] when the semantic ranker is unavailable or errors out."""
        if self._embedder is None or self._rule_vecs is None:
            return []
        try:
            import numpy as np

            qv = self._embedder.embed([query])[0]          # (dim,), normalized
            sims = self._rule_vecs @ qv                      # (n,), cosine
            ranked = [(i, float(sims[i])) for i in candidate_indices if sims[i] > 0]
            ranked.sort(key=lambda x: x[1], reverse=True)
            return ranked[:limit]
        except Exception:
            return []

    def retrieve(
        self,
        query: str,
        domain: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> str:
        """
        Retrieve relevant rules and return a formatted context block.

        Mandatory rules are always included first (no ranking).
        Ranked rules are selected via hybrid BM25 + TF-IDF + RRF.
        A context budget caps total output.
        """
        t0 = time.perf_counter_ns()
        top_k = top_k or self.top_k

        # --- mandatory rules (always present) ---
        mandatory_block = "\n\n".join(r.render() for r in self._mandatory_rules)
        used_tokens = _estimate_tokens(mandatory_block) if mandatory_block else 0

        if not self._ranked_rules or not self._bm25:
            elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
            return self._format_block(mandatory_block, [], elapsed_ms)

        # --- optional domain pre-filter ---
        if domain:
            candidate_indices = [
                i for i, r in enumerate(self._ranked_rules)
                if r.domain == domain
            ]
        else:
            candidate_indices = list(range(len(self._ranked_rules)))

        candidate_set = set(candidate_indices)

        # --- BM25 ---
        query_tokens = _tokenize(query)
        bm25_scores = self._bm25.score(query_tokens)
        bm25_ranked = [
            (i, bm25_scores[i])
            for i in candidate_indices
            if bm25_scores[i] > 0
        ]
        bm25_ranked.sort(key=lambda x: x[1], reverse=True)
        bm25_ranked = bm25_ranked[:top_k * 2]

        # --- TF-IDF ---
        tfidf_ranked = self._tfidf.query(
            query,
            top_k=top_k * 2,
            candidates=candidate_set if domain else None,
        )

        # --- optional semantic (embedding) ranker ---
        ranked_lists = [bm25_ranked, tfidf_ranked]
        embed_ranked = self._embed_rank(query, candidate_indices, top_k * 2)
        if embed_ranked:
            ranked_lists.append(embed_ranked)

        # --- RRF fusion ---
        fused = rrf(ranked_lists)

        # --- apply context budget ---
        selected: list[tuple[Rule, float]] = []
        for idx, score in fused[:top_k]:
            rule = self._ranked_rules[idx]
            rendered = rule.render(score)
            cost = _estimate_tokens(rendered)
            if used_tokens + cost > self.context_budget:
                break
            selected.append((rule, score))
            used_tokens += cost

        elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
        return self._format_block(mandatory_block, selected, elapsed_ms)

    def _format_block(
        self,
        mandatory_block: str,
        selected: list[tuple[Rule, float]],
        elapsed_ms: float,
    ) -> str:
        n_mandatory = len(self._mandatory_rules)
        n_ranked = len(selected)
        total = n_mandatory + n_ranked

        parts = [f"--- WRIT RULES ({total} rules, {elapsed_ms:.2f}ms) ---"]

        if mandatory_block:
            parts.append("")
            parts.append(f"# MANDATORY ({n_mandatory})")
            parts.append(mandatory_block)

        if selected:
            parts.append("")
            parts.append(f"# RELEVANT ({n_ranked})")
            for rule, score in selected:
                parts.append(rule.render(score))
                parts.append("")

        parts.append("--- END WRIT RULES ---")
        return "\n".join(parts)
