"""
Clawness — lightweight hybrid rule retrieval for AI coding agents.

Keeps the core ideas (hybrid ranking, mandatory rules, context budgets — see the
upstream credit in README) but drops Neo4j, ONNX, Docker, and the FastAPI daemon.
The entire retriever runs in-process in pure Python.

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
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------

# Placeholder any rule's text may contain; replaced at render time with the live
# month + year (e.g. "June 2026"). Lets a rule stay accurate without edits as time
# passes — the hook is a fresh process every prompt, so the date is always current.
_DATE_TOKEN = "{{CURRENT_DATE}}"


def _current_date() -> str:
    """Current month and year, e.g. 'June 2026'."""
    return datetime.now().strftime("%B %Y")


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

    # ---- version provenance (optional; see CLAUDE.md "corpus staleness") ----
    # applies_to maps a *detector label* ("Next.js" — the key `scan_project`
    # emits, not the package name) to a version range the rule was established
    # against. verified is the review date (YYYY-MM); sources are what justified
    # the range. All three default empty and a rule without them behaves exactly
    # as before. Deliberately NOT in build_search_text: provenance must never
    # move a retrieval score.
    applies_to: dict[str, str] = field(default_factory=dict)
    verified: str = ""
    sources: list[str] = field(default_factory=list)

    # ---- derived fields (populated at index time) ----
    _search_text: str = field(default="", repr=False)

    def build_search_text(self) -> str:
        """Concatenate all searchable fields into one string for indexing.

        Version provenance (applies_to/verified/sources) is excluded on purpose —
        including it would let a stamp shift retrieval scores, so stamping a rule
        could silently change which rules surface."""
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

    def render(self, relevance: float | None = None, compact: bool = False) -> str:
        """Format for injection into agent context.

        *relevance* is the rule's TF-IDF cosine for the query (the same 0..1 signal
        the relevance floor uses), shown so the number is meaningful and comparable
        to CLAW_MIN_RELEVANCE — unlike the rank-based RRF score, which is ~0.03 for
        everything and misleading to display.

        compact=True emits only the id header + RULE directive, dropping
        WHEN/BAD/GOOD. Used for always-on mandatory rules, whose WHEN/BAD/GOOD
        examples are identical every turn — re-sending them is pure repetition.
        """
        rel_str = f" relevance={relevance:.3f}" if relevance is not None else ""
        lines = [f"[{self.id}] ({self.domain}/{self.severity}){rel_str}"]
        if not compact and self.when:
            lines.append(f"  WHEN: {self.when}")
        if self.rule:
            lines.append(f"  RULE: {self.rule}")
        if not compact:
            if self.violation:
                lines.append(f"  BAD:  {self.violation}")
            if self.correct:
                lines.append(f"  GOOD: {self.correct}")
        out = "\n".join(lines)
        # Substitute the dynamic-date placeholder only at render (not in the search
        # text), so retrieval stays date-independent while the injected rule always
        # shows the live month + year. Computed lazily — only if a token is present.
        if _DATE_TOKEN in out:
            out = out.replace(_DATE_TOKEN, _current_date())
        return out


# ---------------------------------------------------------------------------
# Rule loader
# ---------------------------------------------------------------------------

def _parse_applies_to(raw: object) -> dict[str, str]:
    """Coerce a rule's `applies_to:` into {label: range}, dropping anything
    malformed. A stamp that can't be read must not raise in the prompt hook and
    must not half-arm the staleness check — an unusable stamp is no stamp.
    `clawness lint` is where a malformed one gets reported loudly."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for label, spec in raw.items():
        if label is None or spec is None:
            continue
        label_s = str(label).strip()
        spec_s = str(spec).strip()
        if label_s and spec_s:
            out[label_s] = spec_s
    return out


def _replace_by_id(existing: list[Rule], incoming: list[Rule]) -> list[Rule]:
    """Merge *incoming* into *existing*, replacing same-id rules in place and
    appending the rest. Within *incoming*, the last rule with a given id wins.
    See `Clawness.add_rules` for why this applies to ranked rules only."""
    merged = list(existing)
    positions = {r.id: i for i, r in enumerate(merged)}
    for rule in incoming:
        pos = positions.get(rule.id)
        if pos is None:
            positions[rule.id] = len(merged)
            merged.append(rule)
        else:
            merged[pos] = rule
    return merged


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
        # Always decode rule YAML as UTF-8. Without this, open() uses the locale
        # default (cp1252 on Windows), which mangles em-dashes/smart-quotes in the
        # rules into mojibake (— → â€") at load time — before any rendering.
        # Strict UTF-8 raises on a genuinely malformed file, so skip any file that
        # won't decode or parse — one bad rule must never crash the prompt hook.
        # (`clawness lint` surfaces such files loudly; see cmd_lint.)
        try:
            with open(yml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
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
            applies_to=_parse_applies_to(data.get("applies_to")),
            verified=str(data.get("verified") or "").strip(),
            sources=[str(s) for s in (data.get("sources") or []) if s is not None],
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

# Seed contents for a fresh .clawness/memory.md. The `## Always` section is
# seeded (empty) so the pinned-vs-ranked distinction is discoverable without
# reading the docs. HTML comments here are for the human editing the file —
# they're stripped before injection, so they cost nothing per turn.
MEMORY_TEMPLATE = """\
# Project lessons (Clawness memory)
<!-- Clawness retrieves from this file each prompt: `## Always` entries are
     injected every turn (keep to 3), `## Lessons` entries only when they match
     the prompt. Tell Claude "remember this: ..." or append a bullet yourself
     (one line, <=120 chars, newest at the bottom). Only lessons that would cost
     real rework if forgotten belong here — not a session log. See ENF-MEM-001. -->

## Always

## Lessons
"""


def render_memory_block(
    memory_path: str | Path,
    char_budget: int = 1200,
    query: str | None = None,
    top_k: int | None = None,
    min_relevance: float | None = None,
    pin_budget: int | None = None,
    force_recent: bool = False,
) -> str:
    """
    Render a per-project lessons-learned file as an injectable block.

    Thin re-export of `clawness.memory.render_memory_block`, kept here because
    this is the import path the hook and callers already use. The import is
    deferred to the call: `clawness.memory` imports the ranking primitives from
    this module, so a module-level import would be circular.
    """
    from .memory import render_memory_block as _render

    return _render(
        memory_path,
        char_budget=char_budget,
        query=query,
        top_k=top_k,
        min_relevance=min_relevance,
        pin_budget=pin_budget,
        force_recent=force_recent,
    )


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
        "assertion", "assert", "deterministic", "determinism", "seed", "flaky",
        "snapshot", "e2e", "integration",
    ),
    "__security__": (
        "security", "secure", "vulnerability", "vulnerabilities", "vuln",
        "xss", "csrf", "injection", "sanitize", "sanitization", "exploit",
        "exploits", "harden", "hardening", "owasp", "ssrf", "traversal", "idor",
        "deserialization", "crypto", "cryptography", "encryption", "hashing",
        "bcrypt", "argon2", "tls",
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
    # NOTE: "actions" is deliberately absent. It already drags NX-ACTION-001
    # (Next.js Server Actions) onto GitHub-Actions queries; adding it here would
    # strengthen that false positive rather than fix it. CI-PIN-001 and friends
    # disambiguate on "workflow"/"runner"/"oidc" instead.
    "__build__": (
        "build", "ci", "cicd", "pipeline", "compile", "compiler", "bundle",
        "bundler", "webpack", "vite", "rollup", "lint", "linter", "eslint",
        "prettier", "format", "formatter", "github", "oidc", "runner",
        "deploy", "deployment",
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
    # Building WITH an LLM (the user's own agent/app), not Claude's own conduct.
    # Deliberately excludes token/session/context/index: those already belong to
    # __auth__/__db__, and cross-wiring them surfaces LLM rules on auth prompts.
    "__llm__": (
        "llm", "ai", "model", "models", "prompt", "prompts", "completion",
        "chat", "embedding", "embeddings", "rag", "inference", "hallucination",
        "anthropic", "openai", "claude", "gpt", "finetune", "finetuning",
        "temperature", "agentic",
    ),
    "__science__": (
        "dimensional", "dimension", "dimensions", "units", "si", "physical",
        "equation", "equations", "derivation", "derive", "analytic", "numerical",
        "numerics", "simulation", "solver", "uncertainty", "sigma", "significance",
        "statistical", "arxiv", "paper", "papers", "manuscript", "preprint",
        "reproducible", "reproducibility", "benchmark", "calibration",
        # Numerics and scientific data vocabulary. Retrieval is lexical, so a rule
        # about array dtypes is unreachable from "dataframe" without this bridge.
        # Deliberately EXCLUDES "convergence", "residual", "boundary" and "grid":
        # those read as ordinary dev words (a converging estimate, a residual bug,
        # a boundary case, a CSS grid) and would drag science/cfd rules onto
        # routine prompts — exactly the 1.3.0 noise the topical floor exists for.
        "quadrature", "interpolation", "ode", "pde", "tensor", "vectorize",
        "vectorise", "vectorized", "vectorised", "dataframe", "notebook",
        "notebooks", "jupyter", "ipynb", "numpy", "scipy", "pandas", "matlab",
        "julia", "fortran", "hdf5", "netcdf", "parquet", "dtype", "nan",
        "mpi", "openmp", "hpc", "cluster", "parallelize", "parallelise",
    ),
    # CFD/engineering simulation. Its own marker rather than more __science__
    # terms: these words are worthless outside a CFD case and the domain is
    # stack-gated, so widening __science__ with them would raise noise on every
    # science prompt for no gain. "mesh" is the anchor term users actually type.
    "__cfd__": (
        "cfd", "mesh", "meshing", "openfoam", "fluent", "ansys", "starccm",
        "turbulence", "turbulent", "laminar", "courant", "cfl", "yplus",
        "rans", "les", "des", "reynolds", "navier", "stokes", "aerodynamics",
        "aerodynamic", "drag", "lift", "wake", "vortex", "shedding", "inlet",
        "outlet", "skewness", "snappyhexmesh", "checkmesh", "gci",
    ),
    "__resilience__": (
        "timeout", "timeouts", "retry", "retries", "backoff", "jitter",
        "idempotent", "idempotency", "circuit", "breaker", "resilience",
        "outage", "degrade", "throttle", "deadline", "failover",
    ),
    # Information-gathering discipline + the research programme itself.
    # Deliberately excludes "open" (far too common: "open a file") and "review"
    # (owned by code review — it would drag WF-CODE-REVIEW-001 onto literature
    # queries and literature rules onto PR reviews).
    "__research__": (
        "research", "source", "sources", "cite", "citation", "citations",
        "primary", "evidence", "claim", "claims", "corroborate", "stale",
        "outdated", "provenance", "frontier", "unsolved", "novelty", "novel",
        "gap", "gaps", "survey", "literature", "synthesis", "synthesise",
        "synthesize", "analogy", "analogous", "interdisciplinary", "hypothesis",
        # Shared with __science__ on purpose: a term may carry several markers.
        "paper", "papers", "preprint", "arxiv",
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

    def build(
        self, documents: list[str], tokenized: Optional[list[list[str]]] = None
    ) -> None:
        """Build the index. *tokenized* lets a caller that has already run
        `_tokenize` over the same documents (the memory ranker builds a BM25 index
        first) hand the result in, skipping a full duplicate tokenize pass — the
        single most expensive step here. Omit it and the documents are tokenized
        as before."""
        self._n_docs = len(documents)
        if tokenized is None:
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


# Language/framework domains — these are penalized with a higher relevance floor
# when they're NOT part of the project's detected stack, so a Python repo doesn't
# surface scattershot SQL/Capacitor/React rules on a vague prompt. Everything else
# (general, meta, workflows, security, testing, ci, reliability, research, science)
# is cross-cutting — it applies regardless of stack and always uses the base floor.
#
# science/ and research/ are cross-cutting ON PURPOSE despite having detectors:
# a researcher often works in a bare directory or a LaTeX folder with no
# detectable stack, and gating would silence those rules exactly where they are
# needed most. Their precision comes from tight triggers, not from the floor.
_STACK_DOMAINS = frozenset({
    "python", "fastapi", "typescript", "react", "nextjs", "capacitor",
    "go", "rust", "java", "sql", "bash", "css", "docker",
    # "llm" is stack-gated like a language/framework: prompt-caching and
    # eval-set rules are noise in a repo that calls no model. Detected from
    # anthropic/openai/langchain deps (see init.py).
    "llm",
    # Scientific-computing languages. Stack-gated for the same reason as the web
    # ones — Fortran column-major advice on a TypeScript prompt is pure noise —
    # and NOT topical like science/, because these have unambiguous detectors
    # (Project.toml, *.f90, *.mlx, DESCRIPTION) so a real user of them is never
    # in the "bare directory" case that made science/ un-gateable.
    "julia", "fortran", "matlab", "r",
    # cfd/ is gated hardest of all: mesh, turbulence and Courant-number advice is
    # actively misleading anywhere else, and the vocabulary ("solver",
    # "convergence", "residual", "boundary") collides with ordinary dev language.
    "cfd",
})

# Cross-cutting but topically NARROW. These are never stack-gated — a researcher
# often works in a bare or LaTeX-only directory where nothing is detected, and
# gating would silence them exactly there. But unlike general/meta/workflows they
# don't genuinely apply to every prompt, and at the base floor they fill top-k
# slots on ordinary coding work: measured at 1.3.0, 11 of 30 routine dev prompts
# surfaced one ("write a test for this" -> SCI-PAPER-001, "the build is failing"
# -> RES-NOVELTY-001). A middle floor keeps them un-gated while demanding a real
# match. Genuine science/research questions score 0.20-0.45, far above it, so the
# bare-directory case is unaffected; the noise band is 0.06-0.12.
_TOPICAL_DOMAINS = frozenset({"science", "research"})

# The mirror of _TOPICAL_DOMAINS: stack-gated AND vocabulary-colliding, so they take
# a floor ABOVE the ordinary off-stack one when the project isn't theirs.
#
# Their core words are ordinary dev words. "the solver is not converging, fix the
# residual bug" in a Python repo scored CFD-CONVERGE-001 at 0.190 — clearing the 0.15
# off-stack floor — and "vectorize this dataframe loop" pulled in MATLAB and R at
# 0.193/0.163. Measured over routine dev prompts the collision band tops out at 0.193,
# while an explicit ask ("which turbulence model for this openfoam case", "fix the
# type instability in my julia function") starts at 0.264; 0.22 sits in that gap.
#
# The higher bar costs these domains nothing where they matter: in their OWN project
# they're on-stack, so this floor never applies and they rank at the base 0.06. It only
# governs the case where someone in a Node or Python repo says "solver" — and there,
# Fortran array-ordering advice was never the answer. Unlike sql/docker (legitimately
# cross-stack — a Python service does talk to Postgres), there is no such thing as
# needing Fortran conventions while writing TypeScript.
_NARROW_STACK_DOMAINS = frozenset({"cfd", "julia", "fortran", "matlab", "r"})


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
        stack_domains: Optional[Iterable[str]] = None,  # project's detected stack
        off_stack_min_relevance: Optional[float] = None,  # higher floor for off-stack
        topical_min_relevance: Optional[float] = None,  # middle floor for science/research
        narrow_min_relevance: Optional[float] = None,  # top floor for off-stack cfd/julia/...
        build_index: bool = True,       # False: caller will add_rules() then build_index()
    ) -> None:
        self.rules_dir = Path(rules_dir)
        self.context_budget = context_budget
        self.top_k = top_k

        # The project's detected stack (e.g. {"python","fastapi"}). When provided,
        # language/framework rules from OTHER stacks must clear a higher floor
        # (off_stack_min_relevance) to be injected — so a vague prompt in a Python
        # repo doesn't surface SQL/Capacitor/React noise, while a genuinely strong
        # cross-domain match still gets through (preserving mid-session relevance
        # when a new dependency is added). None (the CLI/eval default) disables the
        # penalty entirely, so retrieval there is unchanged.
        self.stack_domains = set(stack_domains) if stack_domains is not None else None

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

        # Higher floor applied to off-stack language/framework rules (see
        # stack_domains above). Never below the base floor. Tunable via
        # CLAW_OFFSTACK_MIN_RELEVANCE.
        if off_stack_min_relevance is None:
            try:
                off_stack_min_relevance = float(
                    os.environ.get("CLAW_OFFSTACK_MIN_RELEVANCE", "0.15")
                )
            except ValueError:
                off_stack_min_relevance = 0.15
        self.off_stack_min_relevance = max(self.min_relevance, off_stack_min_relevance)

        # Middle floor for topically-narrow cross-cutting domains (see
        # _TOPICAL_DOMAINS). Sits between the base and off-stack floors: high
        # enough to drop the 0.06-0.12 noise band that put science/research rules
        # into ordinary coding results, low enough that a real question (0.20+)
        # is untouched even in a directory where nothing is detected. Never below
        # the base floor. Tunable via CLAW_TOPICAL_MIN_RELEVANCE; set it to the
        # base floor to restore 1.3.0 behaviour.
        if topical_min_relevance is None:
            try:
                topical_min_relevance = float(
                    os.environ.get("CLAW_TOPICAL_MIN_RELEVANCE", "0.12")
                )
            except ValueError:
                topical_min_relevance = 0.12
        self.topical_min_relevance = max(self.min_relevance, topical_min_relevance)

        # Top floor, for off-stack rules from the vocabulary-colliding domains (see
        # _NARROW_STACK_DOMAINS). Applies only when the project's stack is known and
        # isn't theirs; in their own project they fall through to the base floor.
        # Never below the off-stack floor. Tunable via CLAW_NARROW_MIN_RELEVANCE.
        if narrow_min_relevance is None:
            try:
                narrow_min_relevance = float(
                    os.environ.get("CLAW_NARROW_MIN_RELEVANCE", "0.22")
                )
            except ValueError:
                narrow_min_relevance = 0.22
        self.narrow_min_relevance = max(self.off_stack_min_relevance,
                                        narrow_min_relevance)

        # Rendering verbosity (token efficiency). Mandatory rules repeat on
        # every turn, so they render compact (id + RULE only) unless
        # CLAW_VERBOSE is set. Ranked rules render full (with WHEN/BAD/GOOD)
        # unless CLAW_COMPACT trims them too. CLAW_VERBOSE also re-enables the
        # retrieval metadata (relevance scores, timing) in the block — hidden by
        # default because those values change every turn, making an otherwise
        # identical block byte-different (which defeats provider prompt caching)
        # while telling the model nothing actionable.
        self._verbose = bool(os.environ.get("CLAW_VERBOSE"))
        self._mandatory_compact = not self._verbose
        self._ranked_compact = bool(os.environ.get("CLAW_COMPACT"))

        # load
        self._ranked_rules, self._mandatory_rules = load_rules(self.rules_dir)
        self._bm25: Optional[BM25] = None
        self._tfidf: Optional[TfIdfIndex] = None
        self._indexed = False

        if build_index:
            self.build_index()

    def add_rules(self, ranked: list["Rule"], mandatory: list["Rule"]) -> None:
        """Merge additional rules (e.g. a project's `.clawness/rules/`) into the
        corpus. Call `build_index()` once after all `add_rules()` calls are done —
        this does not rebuild the index itself, so multiple merges stay cheap.

        An incoming **ranked** rule whose id already exists replaces the existing
        one, in place. This is what makes `.clawness/rules/` the override layer it
        is documented to be: before 1.9.0 this appended, so a project rule and the
        global rule it meant to override both entered the corpus and competed on
        lexical score — the stale global copy won a query that named the newer
        version. Position is preserved, so merge order can't perturb ranking.

        **Mandatory rules are appended, never replaced, and that asymmetry is
        deliberate.** `.clawness/rules/` is project-local content, so it is
        exactly the untrusted surface ENF-SEC-006 is about: a cloned repo
        shipping `_mandatory/ENF-SEC-006.yml` would, under replacement, silently
        remove the real one from every turn. Appending leaves the genuine rule
        rendering (alongside a visible impostor) — noisier, but the invariant
        holds. The feature that needed replacement is version overrides of ranked
        framework rules, which this costs nothing."""
        self._ranked_rules = _replace_by_id(self._ranked_rules, ranked)
        self._mandatory_rules += mandatory
        self._indexed = False

    def build_index(self) -> None:
        """Build (or rebuild) the BM25/TF-IDF indexes over the current ranked
        corpus. Runs automatically in `__init__` unless `build_index=False` was
        passed — callers that merge project rules via `add_rules()` first (e.g.
        the hook, avoiding a wasted build-then-rebuild) must call this once
        after. `retrieve()`/`rank_ids()` raise if this hasn't run yet."""
        if not self._ranked_rules:
            self._bm25 = None
            self._tfidf = None
            self._indexed = True
            return

        search_texts = [r._search_text for r in self._ranked_rules]
        tokenized = [_tokenize(t) for t in search_texts]

        self._bm25 = BM25()
        self._bm25.build(tokenized)

        self._tfidf = TfIdfIndex()
        self._tfidf.build(search_texts)
        self._indexed = True

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
        if not self._indexed:
            raise RuntimeError(
                "build_index() must be called before retrieve()/rank_ids() — "
                "this Clawness instance was constructed with build_index=False "
                "(or add_rules() was called since the last build)."
            )
        # _bm25 and _tfidf are built together (both None only when there are no
        # ranked rules); narrow both so the type checker is satisfied below.
        if not self._ranked_rules or self._bm25 is None or self._tfidf is None:
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

        # --- RRF fusion (determines ordering) ---
        fused = rrf([bm25_ranked, tfidf_ranked])

        # --- attach TF-IDF relevance + apply the floor ---
        # Ordering stays RRF (the fusion of both signals), but each rule carries
        # its TF-IDF cosine as the reported score. That cosine is the calibrated
        # 0..1 relevance signal the floor is gauged on — and what callers should
        # display. RRF scores are rank-based (~0.03 for everything regardless of
        # match strength), so showing them against the floor is misleading. The
        # floor drops candidates below the noise threshold so a signal-less prompt
        # returns few/no scattershot rules. Disabled when min_relevance == 0.
        tfidf_map = dict(tfidf_ranked)
        ranked: list[tuple[int, float]] = []
        for i, _rrf in fused:
            relevance = tfidf_map.get(i, 0.0)
            ranked.append((i, relevance))

        # --- apply the floor (per-rule: off-stack rules face a higher bar) ---
        floored = [
            (i, rel) for (i, rel) in ranked
            if rel >= self._floor_for(self._ranked_rules[i].domain)
        ]

        # --- BM25 rescue ---
        # RRF fuses both signals, but the floor above is gauged on TF-IDF cosine
        # alone (see the comment on `tfidf_map` above) — so a rule BM25 ranks
        # confidently #1 (e.g. a rare-trigger-token match) can carry TF-IDF
        # relevance 0.0 and be dropped anyway, half-neutralizing the fusion.
        # Rescue only when the floor emptied the result ENTIRELY: this can only
        # ADD a candidate, never crowd one out, so a query that already clears
        # the floor via TF-IDF (including a noise/signal-less prompt, which
        # empirically still returns something today) is completely unaffected.
        # BM25 magnitudes aren't calibrated across queries (a noise prompt's top
        # score can exceed a genuine narrow query's), so a ratio/absolute
        # threshold can't be tuned reliably — "floor emptied the result" is the
        # only additive, zero-regression trigger condition.
        if not floored and bm25_ranked:
            top_idx, top_score = bm25_ranked[0]
            if top_score > 0:
                floored = [(top_idx, tfidf_map.get(top_idx, 0.0))]

        return floored

    def _floor_for(self, domain: str) -> float:
        """Relevance floor for a rule's domain. Language/framework rules from a
        stack the project doesn't use must clear the higher off-stack floor;
        topically-narrow cross-cutting rules must clear the topical floor;
        everything else uses the base floor.

        The narrow tier is checked FIRST inside the off-stack branch: cfd/julia/
        fortran/matlab/r are also in _STACK_DOMAINS, so returning the ordinary
        off-stack floor before testing them would make the tier dead code."""
        if (self.stack_domains is not None
                and domain in _STACK_DOMAINS
                and domain not in self.stack_domains):
            if domain in _NARROW_STACK_DOMAINS:
                return self.narrow_min_relevance
            return self.off_stack_min_relevance
        if domain in _TOPICAL_DOMAINS:
            return self.topical_min_relevance
        return self.min_relevance

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
        show_meta: Optional[bool] = None,
        abbreviate_mandatory: bool = False,
    ) -> str:
        """
        Retrieve relevant rules and return a formatted context block.

        Mandatory rules are always included first (no ranking).
        Ranked rules are selected via hybrid BM25 + TF-IDF + RRF.
        A context budget caps total output.

        *show_meta* controls the per-turn retrieval metadata (relevance scores,
        timing in the header). Default (None) follows CLAW_VERBOSE — off for the
        hook so the injected block stays byte-stable across turns (prompt-cache
        friendly); the CLI passes True since its output isn't model context.

        *abbreviate_mandatory*, when True, renders the mandatory block as a
        single id-list line instead of the full rule text — the hook uses this
        on turns where the full block was already shown earlier this session
        (see clawness/session_state.py). The rules stay just as binding; only
        their re-statement is compressed.
        """
        if not self._indexed:
            raise RuntimeError(
                "build_index() must be called before retrieve()/rank_ids() — "
                "this Clawness instance was constructed with build_index=False "
                "(or add_rules() was called since the last build)."
            )
        t0 = time.perf_counter_ns()
        top_k = top_k or self.top_k
        if show_meta is None:
            show_meta = self._verbose

        # --- mandatory rules (always present) ---
        if abbreviate_mandatory and self._mandatory_rules:
            ids = ", ".join(r.id for r in self._mandatory_rules)
            mandatory_block = f"MANDATORY (in context above, still binding): {ids}"
        else:
            mandatory_block = "\n\n".join(
                r.render(compact=self._mandatory_compact) for r in self._mandatory_rules
            )
        used_tokens = _estimate_tokens(mandatory_block) if mandatory_block else 0

        if not self._ranked_rules or not self._bm25:
            elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
            return self._format_block(mandatory_block, [], elapsed_ms, show_meta, abbreviate_mandatory)

        # --- rank ranked-corpus candidates (idx, TF-IDF relevance) ---
        ranked = self._rank(query, domain, top_k)

        # --- apply context budget ---
        selected: list[tuple[Rule, float]] = []
        for idx, relevance in ranked[:top_k]:
            rule = self._ranked_rules[idx]
            rendered = rule.render(relevance, compact=self._ranked_compact)
            cost = _estimate_tokens(rendered)
            if used_tokens + cost > self.context_budget:
                break
            selected.append((rule, relevance))
            used_tokens += cost

        elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
        return self._format_block(mandatory_block, selected, elapsed_ms, show_meta, abbreviate_mandatory)

    def _format_block(
        self,
        mandatory_block: str,
        selected: list[tuple[Rule, float]],
        elapsed_ms: float,
        show_meta: bool = False,
        mandatory_abbreviated: bool = False,
    ) -> str:
        n_mandatory = len(self._mandatory_rules)
        n_ranked = len(selected)
        total = n_mandatory + n_ranked

        # Timing/scores are diagnostics: they vary every turn, so embedding them
        # would make an otherwise identical block byte-different each prompt
        # (breaking prompt-cache reuse) for zero benefit to the model.
        if show_meta:
            parts = [f"--- CLAWNESS RULES ({total} rules, {elapsed_ms:.2f}ms) ---"]
        else:
            parts = ["--- CLAWNESS RULES ---"]

        if mandatory_block:
            parts.append("")
            if mandatory_abbreviated:
                # The one-liner already says "MANDATORY" — no separate header.
                parts.append(mandatory_block)
            else:
                parts.append(f"# MANDATORY ({n_mandatory})")
                parts.append(mandatory_block)

        if selected:
            parts.append("")
            parts.append(f"# RELEVANT ({n_ranked})")
            for rule, relevance in selected:
                parts.append(rule.render(relevance if show_meta else None,
                                         compact=self._ranked_compact))
                parts.append("")

        parts.append("--- END CLAWNESS RULES ---")
        return "\n".join(parts)
