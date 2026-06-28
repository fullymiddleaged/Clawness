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
  6. Reciprocal Rank Fusion merges the ranked lists
  7. Context budget caps total output tokens

No models, no embeddings, no services — the concept layer (step 3) gives the
"different words, same idea" reach that a vector model would, instantly.

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

    def render(self, score: float | None = None, compact: bool = False) -> str:
        """Format for injection into agent context.

        compact=True emits only the id header + RULE directive, dropping
        WHEN/BAD/GOOD. Used for always-on mandatory rules, whose WHEN/BAD/GOOD
        examples are identical every turn — re-sending them is pure repetition.
        """
        score_str = f" score={score:.3f}" if score is not None else ""
        lines = [f"[{self.id}] ({self.domain}/{self.severity}){score_str}"]
        if not compact and self.when:
            lines.append(f"  WHEN: {self.when}")
        if self.rule:
            lines.append(f"  RULE: {self.rule}")
        if not compact:
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
            when=str(data.get("when") or "").strip(),
            rule=str(data.get("rule") or "").strip(),
            violation=str(data.get("violation") or "").strip(),
            correct=str(data.get("correct") or "").strip(),
            source_path=str(yml_path),
        )
        r.build_search_text()

        if is_mandatory:
            mandatory.append(r)
        else:
            ranked.append(r)

    return ranked, mandatory


# ---------------------------------------------------------------------------
# Project memory (per-codebase lessons-learned log)
# ---------------------------------------------------------------------------

# Seed contents for a fresh .clawness/memory.md. Kept lean because the whole
# file is injected into every prompt — the comment is the "it's working + how to
# use it" default line the user sees, and `render_memory_block` adds the upkeep
# footer at injection time, so the file itself stays minimal.
MEMORY_TEMPLATE = """\
# Project lessons (Clawness memory)
<!-- Clawness injects this file into every prompt. Tell Claude "remember this: ..."
     to add a lesson, or append one terse bullet yourself (newest at the bottom).
     Keep it lean and deduplicated — it costs tokens every turn. See WF-LESSONS-001. -->

## Lessons
"""


def render_memory_block(memory_path: str | Path, char_budget: int = 2000) -> str:
    """
    Load a per-project lessons-learned file and render it as an injectable block.

    The memory file is plain markdown the agent maintains itself (one terse
    bullet per hard-won lesson / recurring frustration). It is injected verbatim
    every turn so past lessons become live context — the auto-recalled "memories"
    pattern, but project-local and version-controllable.

    Bounded by *char_budget*: if the file outgrows it we keep the TAIL (most
    recent lessons, appended at the bottom) so the block never blows the hook's
    token budget. Returns "" when the file is missing, empty, or unreadable —
    callers degrade silently.
    """
    path = Path(memory_path)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""
    if not text:
        return ""

    truncated = False
    if len(text) > char_budget:
        text = text[-char_budget:]
        # Drop the partial first line so we start on a clean bullet.
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        truncated = True

    header = "--- CLAWNESS MEMORY (project lessons) ---"
    footer = (
        "Keep this current: when the same mistake or frustration recurs, append a "
        "terse one-line lesson to .clawness/memory.md (newest at the bottom)."
    )
    parts = [header]
    if truncated:
        parts.append("(older lessons trimmed)")
    parts.append(text)
    # Blank line so the upkeep footer never reads as the first lesson entry.
    parts.append("")
    parts.append(footer)
    parts.append("--- END CLAWNESS MEMORY ---")
    return "\n".join(parts)


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
#      written about "authentication". This is our "semantic" layer: it gives
#      the "different words, same idea" reach of a vector model, but instantly
#      and with zero dependencies. Enrich _CONCEPT_GROUPS to extend its reach.
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
        "catch", "rescue", "fault", "result", "unwrap", "recover", "errno",
        "stacktrace", "traceback",
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
        "accessibility", "a11y", "react", "jsx", "tailwind", "flexbox", "grid",
        "hook", "hooks", "rerender",
    ),
    "__api__": (
        "api", "endpoint", "endpoints", "rest", "restful", "graphql", "route",
        "routes", "routing", "controller", "handler", "handlers", "request",
        "requests", "response", "responses", "http", "cors", "middleware",
        "serialization", "payload", "status",
    ),
    "__validation__": (
        "validate", "validation", "validator", "sanitize", "schema", "zod",
        "pydantic", "constraint", "constraints", "untrusted", "escape", "input",
    ),
    "__container__": (
        "docker", "dockerfile", "container", "containers", "image", "images",
        "kubernetes", "k8s", "compose", "pod", "pods",
    ),
    "__null__": (
        "null", "none", "nil", "undefined", "optional", "nullable",
        "nullability", "nonnull", "npe",
    ),
    "__naming__": (
        "naming", "rename", "identifier", "magic", "constant", "constants",
    ),
    "__docs__": (
        "comment", "comments", "docstring", "documentation", "readme",
        "javadoc", "doc", "docs",
    ),
    "__refactor__": (
        "refactor", "refactoring", "cleanup", "duplication", "duplicate",
        "dry", "complexity", "smell", "coupling", "cohesion", "abstraction",
        "yagni",
    ),
    "__immutable__": (
        "immutable", "immutability", "mutation", "mutate", "readonly",
        "frozen", "freeze", "const",
    ),
    "__build__": (
        "build", "ci", "cicd", "pipeline", "compile", "compiler", "bundle",
        "bundler", "webpack", "vite", "rollup", "lint", "linter", "eslint",
        "prettier", "format", "formatter",
    ),
    "__git__": (
        "git", "commit", "commits", "branch", "branches", "merge", "rebase",
        "pr", "diff", "vcs", "gitignore",
    ),
    "__shell__": (
        "shell", "bash", "sh", "posix", "shellcheck",
    ),
    "__mobile__": (
        "capacitor", "ios", "android", "mobile", "native", "webview", "cordova",
    ),
    "__shortcut__": (
        "shortcut", "hack", "temporary", "temporarily", "quick", "simple",
        "trivial", "obvious", "later", "assume", "assumption", "skip", "lazy",
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


class Clawness:
    """
    Lightweight hybrid retriever.

    Usage:
        wl = Clawness("/path/to/rules")
        block = wl.retrieve("implement async endpoint for user creation")
        print(block)
    """

    def __init__(
        self,
        rules_dir: str | Path,
        context_budget: int = 4000,     # max tokens for rule block
        top_k: int = 5,                 # max ranked rules to return
        min_relevance: Optional[float] = None,  # TF-IDF cosine floor for ranked rules
    ) -> None:
        self.rules_dir = Path(rules_dir)
        self.context_budget = context_budget
        self.top_k = top_k

        # Relevance floor: a ranked rule is only injected if its TF-IDF cosine
        # clears this bar. RRF scores are rank-based (they don't encode match
        # strength), so without a floor a signal-less prompt still fills every
        # slot with weak, scattershot matches. TF-IDF cosine is the discriminating
        # signal — genuine matches sit well above the ~0.05–0.08 noise tail. The
        # strong matches the eval checks are far above it, so this trims noise
        # without hurting recall. Tunable via CLAW_MIN_RELEVANCE; 0 disables.
        if min_relevance is None:
            try:
                min_relevance = float(os.environ.get("CLAW_MIN_RELEVANCE", "0.06"))
            except ValueError:
                min_relevance = 0.06
        self.min_relevance = max(0.0, min_relevance)

        # Rendering verbosity (token efficiency). Mandatory rules repeat on
        # every turn, so they render compact (id + RULE only) unless
        # CLAW_VERBOSE is set. Ranked rules render full (with WHEN/BAD/GOOD)
        # unless CLAW_COMPACT trims them too.
        self._mandatory_compact = not os.environ.get("CLAW_VERBOSE")
        self._ranked_compact = bool(os.environ.get("CLAW_COMPACT"))

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

    @property
    def stats(self) -> dict:
        return {
            "ranked_rules": len(self._ranked_rules),
            "mandatory_rules": len(self._mandatory_rules),
            "total_rules": len(self._ranked_rules) + len(self._mandatory_rules),
            "rules_dir": str(self.rules_dir),
            "mandatory_tokens": self.mandatory_token_estimate(),
            "context_budget": self.context_budget,
            "top_k": self.top_k,
        }

    def mandatory_token_estimate(self) -> int:
        """Approx tokens the always-on mandatory block adds to every turn
        (honors the compact/verbose rendering setting)."""
        block = "\n\n".join(
            r.render(compact=self._mandatory_compact) for r in self._mandatory_rules
        )
        return _estimate_tokens(block) if block else 0

    def _rank(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[tuple[int, float]]:
        """Hybrid BM25 + TF-IDF ranking, fused via RRF (both run over the
        concept-expanded token stream). Returns fused (rule_index, score) for
        the ranked corpus, best first."""
        if not self._ranked_rules or not self._bm25:
            return []

        limit = limit or self.top_k

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
            (i, bm25_scores[i]) for i in candidate_indices if bm25_scores[i] > 0
        ]
        bm25_ranked.sort(key=lambda x: x[1], reverse=True)
        bm25_ranked = bm25_ranked[:limit * 2]

        # --- TF-IDF ---
        tfidf_ranked = self._tfidf.query(
            query,
            top_k=limit * 2,
            candidates=candidate_set if domain else None,
        )

        # --- RRF fusion ---
        fused = rrf([bm25_ranked, tfidf_ranked])

        # --- relevance floor ---
        # Drop fused candidates whose TF-IDF cosine is below the noise floor, so a
        # signal-less prompt returns few/no scattershot rules instead of filling
        # top_k with coincidental token matches. Gauged on TF-IDF (the calibrated
        # 0..1 relevance signal), not RRF (rank-based) or BM25 (magnitudes overlap
        # noise). Disabled when min_relevance == 0.
        if self.min_relevance > 0.0:
            tfidf_map = dict(tfidf_ranked)
            fused = [
                (i, s) for (i, s) in fused
                if tfidf_map.get(i, 0.0) >= self.min_relevance
            ]
        return fused

    def rank_ids(
        self,
        query: str,
        domain: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> list[str]:
        """Ranked rule IDs (best first) for a query — used by eval/diagnostics."""
        limit = top_k or self.top_k
        return [self._ranked_rules[i].id for i, _ in self._rank(query, domain, limit)[:limit]]

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
        mandatory_block = "\n\n".join(
            r.render(compact=self._mandatory_compact) for r in self._mandatory_rules
        )
        used_tokens = _estimate_tokens(mandatory_block) if mandatory_block else 0

        if not self._ranked_rules or not self._bm25:
            elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
            return self._format_block(mandatory_block, [], elapsed_ms)

        # --- rank ranked-corpus candidates ---
        fused = self._rank(query, domain, top_k)

        # --- apply context budget ---
        selected: list[tuple[Rule, float]] = []
        for idx, score in fused[:top_k]:
            rule = self._ranked_rules[idx]
            rendered = rule.render(score, compact=self._ranked_compact)
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

        parts = [f"--- CLAWNESS RULES ({total} rules, {elapsed_ms:.2f}ms) ---"]

        if mandatory_block:
            parts.append("")
            parts.append(f"# MANDATORY ({n_mandatory})")
            parts.append(mandatory_block)

        if selected:
            parts.append("")
            parts.append(f"# RELEVANT ({n_ranked})")
            for rule, score in selected:
                parts.append(rule.render(score, compact=self._ranked_compact))
                parts.append("")

        parts.append("--- END CLAWNESS RULES ---")
        return "\n".join(parts)
