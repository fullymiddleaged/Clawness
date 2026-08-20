"""
Deterministic attack-surface enumerator — the discovery half of the security
audit, with zero LLM tokens.

An LLM security scan re-run 5-10 times catches most issues because each run
*wanders* to different files and free-associates about risk. The variance is
almost entirely in **discovery, not judgment**. This module makes discovery
reproducible: a regex/lexical sink+source finder that returns the *same* sorted
candidate list every run, so the model is reduced to adjudicating a fixed short
list instead of re-deriving it.

It is a **tripwire, not a SAST engine** — the same framing as ``guard.py``. It
routes the LLM's attention and makes discovery deterministic; it is not
CodeQL/Semgrep and will both miss things (obfuscation, cross-file taint) and
over-report (a parameterised query that only *looks* concatenated). The
``confidence`` field and the downstream adjudication pass are what turn a raw
candidate into a verdict; the ledger (``findings.py``) records that verdict so it
is not re-litigated.

Everything here is pure logic and unit-testable. It never raises on bad input —
an unreadable file is skipped, and any unexpected error fails toward returning
what was found so far. Opt out with ``CLAW_NO_SCAN``.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# Directories never worth scanning: vendored trees, build output, VCS, caches,
# and Clawness's own state. Modelled on guard.py's provenance skip set.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", "dist", "build", ".next", "out", "target", "vendor",
    ".cache", "site-packages", ".mypy_cache", ".pytest_cache", ".tox",
    ".gradle", "Pods", ".idea", ".vscode", "coverage", ".turbo",
    ".clawness", ".claude",
}

# Only these extensions are read. Kept deliberately source-shaped: secrets and
# config live in the data files too, so a few config formats are included.
_SCAN_EXTS = {
    ".py", ".pyw",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".vue", ".svelte", ".astro",
    ".html", ".htm", ".jinja", ".jinja2", ".j2", ".ejs", ".erb", ".hbs",
    ".rb", ".php", ".phtml", ".go", ".java", ".cs", ".rs", ".kt", ".scala",
    ".sql", ".sh", ".bash",
    ".yml", ".yaml", ".json", ".env", ".ini", ".cfg", ".conf", ".toml", ".properties",
}

_MAX_FILE_BYTES = 1_048_576   # 1 MB — skip generated/minified giants
_MAX_FILES = 20_000           # hard cap so a pathological tree can't hang
_MAX_LINE_LEN = 4_000         # a line longer than this is almost certainly minified


# --- sink/source classes --------------------------------------------------
# Each class maps to a CWE, a shipped rule id for orientation (may be "" where the
# corpus has no dedicated rule), and a severity used by the opt-in `--fail-on` gate.
@dataclass(frozen=True)
class ClassMeta:
    cwe: str
    rule: str
    severity: str          # critical | high | medium | low


CLASS_META: dict[str, ClassMeta] = {
    "sql-injection":     ClassMeta("CWE-89", "SEC-SQLI-001", "critical"),
    "command-injection": ClassMeta("CWE-78", "", "critical"),
    "unsafe-deserialization": ClassMeta("CWE-502", "", "high"),
    "code-eval":         ClassMeta("CWE-95", "", "high"),
    "xss":               ClassMeta("CWE-79", "SEC-XSS-001", "high"),
    "path-traversal":    ClassMeta("CWE-22", "SEC-PATH-001", "high"),
    "broken-authz":      ClassMeta("CWE-639", "SEC-AUTHZ-001", "high"),
    "hardcoded-secret":  ClassMeta("CWE-798", "ENF-SEC-001", "critical"),
    "weak-crypto":       ClassMeta("CWE-327", "SEC-CRYPTO-001", "medium"),
    "ssrf":              ClassMeta("CWE-918", "SEC-SSRF-001", "high"),
}

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# A request-derived value: the tokens that mark data as attacker-controlled. Used
# by the classes (path traversal, SSRF, authz) that are only interesting when the
# sink is fed untrusted input.
_TAINT = r"(?:request|req\.|params|query|args|body|input|payload|flask\.request|self\.request|\$_(?:GET|POST|REQUEST|COOKIE))"


@dataclass(frozen=True)
class Pattern:
    cls: str
    regex: "re.Pattern[str]"
    confidence: str            # high | medium | low
    exts: Optional[frozenset]  # None = any scanned extension


def _p(cls: str, pattern: str, confidence: str, exts: Optional[Iterable[str]] = None) -> Pattern:
    return Pattern(cls, re.compile(pattern), confidence,
                   frozenset(exts) if exts is not None else None)


_PY = (".py", ".pyw")
_JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro")
_TEMPLATES = (".html", ".htm", ".jinja", ".jinja2", ".j2", ".ejs", ".erb", ".hbs", ".vue", ".svelte")

# Ordered by class then specificity. Patterns are line-oriented (the common case);
# a cross-line sink is a known miss — this is a tripwire, not a parser.
_PATTERNS: list[Pattern] = [
    # --- SQL injection (CWE-89) ---
    # execute()/query() whose argument is an f-string, %-format, .format(), or a
    # string concatenation — the dynamic-SQL shapes.
    _p("sql-injection",
       r"(?i)\b(?:execute|executemany|executescript|raw|cursor\.execute)\s*\(\s*[^)]*?"
       r"(?:f['\"]|%\s*[\(a-z_]|\.format\s*\(|['\"]\s*\+|\+\s*['\"])", "high"),
    # JS/TS template-literal interpolation into a query.
    _p("sql-injection",
       r"(?i)\.(?:query|execute|raw)\s*\(\s*`[^`]*\$\{", "high", _JS),
    _p("sql-injection",
       r"(?i)\.(?:query|execute)\s*\([^)]*['\"]\s*\+", "medium", _JS),

    # --- command injection (CWE-78) ---
    _p("command-injection", r"(?i)\bos\.system\s*\(", "high", _PY),
    _p("command-injection", r"(?i)\bshell\s*=\s*True\b", "high", _PY),
    _p("command-injection", r"(?i)\bos\.popen\s*\(", "medium", _PY),
    _p("command-injection",
       r"(?i)\bchild_process\b|\.(?:exec|execSync)\s*\(", "medium", _JS),
    _p("command-injection", r"(?i)\b(?:system|exec|passthru|shell_exec|proc_open)\s*\(",
       "high", (".php", ".phtml")),

    # --- unsafe deserialization (CWE-502) ---
    _p("unsafe-deserialization", r"(?i)\b(?:pickle|cPickle|_pickle)\.(?:loads?|load)\s*\(", "high", _PY),
    _p("unsafe-deserialization", r"(?i)\bmarshal\.loads?\s*\(", "high", _PY),
    # yaml.load without a Safe loader.
    _p("unsafe-deserialization",
       r"(?i)\byaml\.load\s*\((?![^)]*Loader\s*=\s*(?:yaml\.)?(?:Safe|CSafe))", "high", _PY),
    _p("unsafe-deserialization", r"(?i)\b(?:unserialize|yaml_parse)\s*\(", "high", (".php", ".phtml")),

    # --- code eval (CWE-95) ---
    _p("code-eval", r"(?i)(?:^|[^.\w])eval\s*\(", "medium", _PY),
    _p("code-eval", r"(?i)(?:^|[^.\w])exec\s*\(", "medium", _PY),
    _p("code-eval", r"(?i)(?:^|[^.\w])eval\s*\(|\bnew\s+Function\s*\(", "medium", _JS),

    # --- XSS (CWE-79) ---
    _p("xss", r"dangerouslySetInnerHTML", "high", _JS),
    _p("xss", r"(?i)\.innerHTML\s*=", "medium", _JS),
    _p("xss", r"(?i)\bv-html\b", "high", _TEMPLATES),
    _p("xss", r"(?i)document\.write\s*\(", "medium", _JS),
    _p("xss", r"(?i)\|\s*safe\b", "medium", _TEMPLATES),
    _p("xss", r"(?i)\{!!.*!!\}|\{\{\{", "medium", _TEMPLATES),   # Blade {!! !!}, Mustache {{{

    # --- path traversal (CWE-22) — file op fed request data ---
    _p("path-traversal",
       rf"(?i)\b(?:open|send_file|send_from_directory)\s*\([^)]*{_TAINT}", "high", _PY),
    _p("path-traversal",
       rf"(?i)\b(?:readFile|readFileSync|createReadStream|sendFile|res\.download)\s*\([^)]*{_TAINT}",
       "high", _JS),

    # --- broken object authorization (CWE-639) — lookup by id from request, no owner scope ---
    _p("broken-authz",
       rf"(?i)\.(?:get|filter|find|findOne|findById|first)\s*\([^)]*\b(?:id|pk)\b[^)]*{_TAINT}",
       "low"),

    # --- hardcoded secrets (CWE-798) ---
    _p("hardcoded-secret", r"\bAKIA[0-9A-Z]{16}\b", "high"),
    _p("hardcoded-secret", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----", "high"),
    _p("hardcoded-secret", r"\bghp_[0-9A-Za-z]{36}\b", "high"),
    _p("hardcoded-secret", r"\bxox[baprs]-[0-9A-Za-z-]{10,}", "high"),
    # Generic assignment — a literal secret value, not a reference to env/config.
    _p("hardcoded-secret",
       r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|"
       r"client[_-]?secret)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]", "medium"),

    # --- weak crypto / weak randomness (CWE-327 / CWE-330) ---
    _p("weak-crypto", r"(?i)\bhashlib\.(?:md5|sha1)\s*\(", "medium", _PY),
    _p("weak-crypto", r"(?i)\b(?:md5|sha1)\s*\(", "low"),
    _p("weak-crypto", r"(?i)\brandom\.(?:random|randint|choice|randrange)\s*\(", "low", _PY),
    _p("weak-crypto", r"(?i)\bMath\.random\s*\(", "low", _JS),

    # --- SSRF (CWE-918) — outbound request to a request-derived URL ---
    _p("ssrf",
       rf"(?i)\b(?:requests\.(?:get|post|put|delete|head|request)|urlopen|httpx\.(?:get|post))"
       rf"\s*\([^)]*{_TAINT}", "medium", _PY),
    _p("ssrf",
       rf"(?i)\b(?:fetch|axios\.(?:get|post|request))\s*\([^)]*{_TAINT}", "low", _JS),
]

# Lines carrying any of these are dropped BEFORE the secret patterns run: they are
# references to a secret store, not a hardcoded literal. Applied only to the
# generic-assignment secret pattern (the specific ones — AKIA, key headers — are
# self-evidently real regardless of surrounding text).
_SECRET_FALSE_POSITIVE_RE = re.compile(
    r"(?i)(?:os\.environ|getenv|process\.env|import\.meta\.env|"
    r"config\[|settings\.|\bvault\b|secretsmanager|\$\{|<[a-z_]+>|"
    r"example|placeholder|changeme|your[_-]?|xxxx|\.\.\.|dummy|redacted|test[_-]?key)"
)


def scan_disabled() -> bool:
    return bool(os.environ.get("CLAW_NO_SCAN"))


def _normalize(snippet: str) -> str:
    """Collapse whitespace so the id is stable against reformatting on one line."""
    return re.sub(r"\s+", " ", snippet).strip()


def candidate_id(rel_path: str, line: int, cls: str, snippet: str) -> str:
    """Stable id keying on (file, line, class, normalized-snippet) — identical
    across runs on unchanged code, so the ledger dedupes; it shifts when the code
    moves, at which point merge_scan marks the old one `gone` and adds the new."""
    h = hashlib.sha256()
    h.update(rel_path.encode("utf-8"))
    h.update(b"\0")
    h.update(str(line).encode("utf-8"))
    h.update(b"\0")
    h.update(cls.encode("utf-8"))
    h.update(b"\0")
    h.update(_normalize(snippet).encode("utf-8"))
    return h.hexdigest()[:16]


def _iter_files(root: Path) -> list[Path]:
    """Deterministic, bounded walk of scannable source files under *root*."""
    files: list[Path] = []
    frontier = [root]
    while frontier:
        current = frontier.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        frontier.append(entry)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            if entry.suffix.lower() not in _SCAN_EXTS:
                continue
            files.append(entry)
            if len(files) >= _MAX_FILES:
                return sorted(files)
    return sorted(files)


def _scan_file(path: Path, rel_path: str) -> list[dict]:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return []

    ext = path.suffix.lower()
    out: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if len(raw) > _MAX_LINE_LEN:
            continue
        for pat in _PATTERNS:
            if pat.exts is not None and ext not in pat.exts:
                continue
            if not pat.regex.search(raw):
                continue
            if pat.cls == "hardcoded-secret" and pat.confidence == "medium" \
                    and _SECRET_FALSE_POSITIVE_RE.search(raw):
                continue
            meta = CLASS_META[pat.cls]
            snippet = raw.strip()[:200]
            out.append({
                "id": candidate_id(rel_path, lineno, pat.cls, raw),
                "file": rel_path,
                "line": lineno,
                "class": pat.cls,
                "cwe": meta.cwe,
                "rule": meta.rule,
                "severity": meta.severity,
                "confidence": pat.confidence,
                "snippet": snippet,
            })
    return out


def enumerate_candidates(root: "str | Path") -> list[dict]:
    """Enumerate every attack-surface candidate under *root*, deterministically
    sorted (file, line, class). Returns [] when disabled or on any fatal error —
    it never raises.

    Each candidate: ``{id, file, line, class, cwe, rule, severity, confidence,
    snippet}``. At most one candidate per (file, line, class): the first pattern
    of a class to match a line wins, so two SQL patterns on one line don't double
    count.
    """
    if scan_disabled():
        return []
    try:
        root = Path(root).resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []

    seen: set[tuple[str, int, str]] = set()
    candidates: list[dict] = []
    for path in _iter_files(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        for cand in _scan_file(path, rel):
            key = (cand["file"], cand["line"], cand["class"])
            if key in seen:
                continue
            seen.add(key)
            candidates.append(cand)

    candidates.sort(key=lambda c: (c["file"], c["line"], c["class"]))
    return candidates


def coverage_map(root: "str | Path") -> dict:
    """Which files were scanned and which classes exist — the denominator the
    ledger's convergence signal reports against."""
    try:
        root = Path(root).resolve()
    except OSError:
        return {"files_scanned": 0, "classes": sorted(CLASS_META)}
    files = _iter_files(root) if root.is_dir() else []
    return {"files_scanned": len(files), "classes": sorted(CLASS_META)}


def severity_at_least(sev: str, floor: str) -> bool:
    return _SEVERITY_ORDER.get(sev, 0) >= _SEVERITY_ORDER.get(floor, 99)
