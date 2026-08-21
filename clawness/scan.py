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
import json
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
    # Fallback bucket for an ingested SAST/SARIF finding that maps to no native
    # class (item 3). Its cwe/severity come from the SARIF result itself, not from
    # here — this row only supplies the default cwe when the tool named none.
    "sast-other":        ClassMeta("CWE-693", "", "medium"),
}

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# A request-derived value: the tokens that mark data as attacker-controlled. Used
# by the classes (path traversal, SSRF, authz) that are only interesting when the
# sink is fed untrusted input.
_TAINT = (
    r"(?:request|req\.|params|query|args|body|input|payload|flask\.request|self\.request|"
    r"formvalue|getparameter|pathvariable|requestparam|c\.param|"          # Go/Java request idioms
    r"\$_(?:GET|POST|REQUEST|COOKIE))"                                     # PHP superglobals
)


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
_GO = (".go",)
_RB = (".rb",)
_RB_VIEW = (".rb", ".erb")
_JAVA = (".java", ".kt", ".scala")
_CS = (".cs",)
_PHP = (".php", ".phtml")

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

    # ============================ Go (.go) ============================
    _p("sql-injection",
       r"(?i)\.(?:Query|QueryRow|QueryContext|QueryRowContext|Exec|ExecContext)\s*\("
       r"[^)]*(?:fmt\.Sprintf\(|['\"]\s*\+|\+\s*['\"])", "high", _GO),
    # exec.Command invoking a shell — the interpolation-injectable shape (bare
    # exec.Command with discrete args is safe, so only the shell form is flagged).
    _p("command-injection",
       r"(?i)\bexec\.Command(?:Context)?\s*\(\s*[\"'](?:/bin/|/usr/bin/)?"
       r"(?:sh|bash|zsh|cmd|powershell)\b", "high", _GO),
    _p("xss", r"(?i)\btemplate\.HTML\s*\(", "medium", _GO),   # marks a string safe, bypassing escaping
    _p("path-traversal",
       rf"(?i)\b(?:os\.Open|os\.ReadFile|os\.OpenFile|ioutil\.ReadFile|http\.ServeFile)"
       rf"\s*\([^)]*{_TAINT}", "high", _GO),
    _p("weak-crypto", r"(?i)\b(?:md5|sha1)\.(?:New|Sum(?:224)?)\s*\(", "medium", _GO),
    _p("weak-crypto",   # math/rand-only helpers (crypto/rand has no Intn/Float*)
       r"(?i)\brand\.(?:Intn|Int31n?|Int63n?|Float64|Float32)\s*\(", "low", _GO),
    _p("ssrf",
       rf"(?i)\b(?:http\.(?:Get|Post|Head)|http\.NewRequest|client\.(?:Get|Post|Do))"
       rf"\s*\([^)]*{_TAINT}", "low", _GO),

    # ============================ Ruby (.rb) ============================
    _p("sql-injection",
       r"(?i)\.(?:where|find_by_sql|exec_query|execute|select_all|from|order|group|having)"
       r"\s*\(?\s*[\"'][^\"']*(?:#\{|['\"]\s*\+|\+\s*['\"])", "high", _RB),
    _p("command-injection", r"(?i)\b(?:system|exec|spawn)\s*\(", "high", _RB),
    _p("command-injection", r"`[^`]*#\{", "high", _RB),            # backticks with interpolation
    _p("command-injection", r"(?i)%x[\(\{\[/]|IO\.popen\s*\(|Open3\.", "medium", _RB),
    _p("unsafe-deserialization",
       r"(?i)\b(?:YAML\.load|Marshal\.load|Oj\.load)\s*\(", "high", _RB),
    _p("code-eval",
       r"(?i)\b(?:eval|instance_eval|class_eval|module_eval)\s*[\(\s]", "medium", _RB),
    _p("xss", r"(?i)\.html_safe\b|\braw\s*\(|<%=\s*raw\b", "medium", _RB_VIEW),
    _p("path-traversal",
       rf"(?i)\b(?:File\.(?:read|open|new|binread)|IO\.(?:read|binread)|send_file)"
       rf"\s*\([^)]*{_TAINT}", "high", _RB),
    _p("weak-crypto", r"(?i)\bDigest::(?:MD5|SHA1)\b", "medium", _RB),
    _p("ssrf",
       rf"(?i)\b(?:Net::HTTP\.(?:get|post|start)|HTTParty\.(?:get|post)|"
       rf"RestClient\.(?:get|post)|open)\s*\([^)]*{_TAINT}", "low", _RB),

    # ==================== Java / Kotlin / Scala (.java .kt .scala) ====================
    _p("sql-injection",
       r"(?i)\.(?:executeQuery|executeUpdate|execute|createQuery|createNativeQuery|"
       r"prepareStatement|addBatch)\s*\([^)]*(?:['\"]\s*\+|\+\s*['\"]|String\.format\s*\()",
       "high", _JAVA),
    _p("command-injection",
       r"(?i)\bRuntime\.getRuntime\s*\(\s*\)\.exec\s*\(|\bnew\s+ProcessBuilder\s*\(",
       "high", _JAVA),
    _p("unsafe-deserialization",
       r"(?i)\bnew\s+(?:ObjectInputStream|XMLDecoder)\b|\.readObject\s*\(", "high", _JAVA),
    _p("code-eval",
       r"(?i)\bScriptEngineManager\b|\.getEngineByName\s*\(|\bSpelExpressionParser\b|"
       r"\.parseExpression\s*\(", "medium", _JAVA),
    _p("xss",
       rf"(?i)\.getWriter\s*\(\s*\)\.(?:print|println|write)\s*\([^)]*{_TAINT}", "medium", _JAVA),
    _p("path-traversal",
       rf"(?i)\bnew\s+(?:File|FileInputStream|FileReader)\s*\([^)]*{_TAINT}|"
       rf"\b(?:Files\.(?:readAllBytes|readString|readAllLines|newInputStream)|Paths\.get)"
       rf"\s*\([^)]*{_TAINT}", "high", _JAVA),
    _p("weak-crypto",
       r"(?i)\bMessageDigest\.getInstance\s*\(\s*[\"'](?:MD5|SHA-?1)[\"']|"
       r"\bDigestUtils\.(?:md5|sha1)\b", "medium", _JAVA),
    _p("weak-crypto", r"(?i)\bnew\s+(?:java\.util\.)?Random\s*\(", "low", _JAVA),
    _p("ssrf",
       rf"(?i)\b(?:openConnection|getForObject|getForEntity|exchange|newBuilder)"
       rf"\s*\([^)]*{_TAINT}|\bnew\s+(?:[\w.]+\.)?URL\s*\([^)]*{_TAINT}", "low", _JAVA),

    # ============================ C# / .NET (.cs) ============================
    _p("sql-injection",
       r"(?i)\b(?:new\s+SqlCommand|CommandText\s*=|FromSqlRaw|ExecuteSqlRaw|"
       r"ExecuteSqlInterpolated|new\s+MySqlCommand|new\s+NpgsqlCommand)\s*[\(=]"
       r"[^;]*(?:['\"]\s*\+|\+\s*['\"]|\$['\"])", "high", _CS),
    _p("command-injection",
       r"(?i)\bProcess\.Start\s*\(|\bnew\s+ProcessStartInfo\b", "high", _CS),
    _p("unsafe-deserialization",
       r"(?i)\b(?:BinaryFormatter|SoapFormatter|NetDataContractSerializer|LosFormatter|"
       r"ObjectStateFormatter)\b|TypeNameHandling", "high", _CS),
    _p("code-eval", r"(?i)\bCSharpScript\.(?:Run|Eval|Create)", "medium", _CS),
    _p("xss",
       rf"(?i)\bResponse\.Write\s*\([^)]*{_TAINT}|@?Html\.Raw\s*\(", "medium", _CS),
    _p("path-traversal",
       rf"(?i)\b(?:File\.(?:ReadAllText|ReadAllBytes|ReadAllLines|Open|OpenRead)|"
       rf"new\s+FileStream|Path\.Combine)\s*\([^)]*{_TAINT}", "high", _CS),
    _p("weak-crypto",
       r"(?i)\b(?:MD5|SHA1)(?:CryptoServiceProvider|Managed)?\.Create\s*\(|"
       r"\bnew\s+(?:MD5|SHA1)(?:CryptoServiceProvider|Managed)\s*\(", "medium", _CS),
    _p("weak-crypto", r"(?i)\bnew\s+Random\s*\(", "low", _CS),
    _p("ssrf",
       rf"(?i)\b(?:WebRequest\.Create|\.GetAsync|\.GetStringAsync|\.GetByteArrayAsync|"
       rf"DownloadString|DownloadData)\s*\([^)]*{_TAINT}", "low", _CS),

    # ============================ PHP round-out (.php .phtml) ============================
    _p("sql-injection",
       r"(?i)\b(?:mysqli_query|mysql_query|pg_query)\s*\(\s*[\"'][^\"']*(?:\$|['\"]\s*\.)|"
       r"->(?:query|exec)\s*\(\s*[\"'][^\"']*(?:\$|['\"]\s*\.)", "high", _PHP),
    _p("code-eval",
       r"(?i)\beval\s*\(|\bcreate_function\s*\(|\bassert\s*\(\s*['\"]", "medium", _PHP),
    _p("xss", r"(?i)\b(?:echo|print)\b[^;]*\$_(?:GET|POST|REQUEST|COOKIE)", "medium", _PHP),
    _p("path-traversal",
       rf"(?i)\b(?:include|include_once|require|require_once|fopen|file_get_contents|readfile)"
       rf"\b[^;]*{_TAINT}", "high", _PHP),
    _p("weak-crypto", r"(?i)\b(?:mt_rand|rand|uniqid)\s*\(", "low", _PHP),
    _p("ssrf",
       rf"(?i)\bcurl_setopt\s*\([^)]*CURLOPT_URL[^)]*{_TAINT}", "low", _PHP),
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


# --- SARIF / SAST ingestion (item 3) -------------------------------------
# Fold real SAST output (bandit, semgrep, CodeQL, …) in as extra deterministic
# candidates WITHOUT requiring those tools be installed — we ingest their *.sarif
# output only. SARIF is JSON, so this stays within "PyYAML is the only dependency".
# External ids are re-keyed through candidate_id so the ledger's dedup/`gone` logic
# holds, and every finding is mapped onto one of the native CLASS_META classes (or
# the `sast-other` fallback) so cwe/rule/severity handling is uniform downstream.

# CWE number → native class. Kept broad: a tool may tag a related CWE in the family.
_SARIF_CWE_TO_CLASS: dict[str, str] = {
    "89": "sql-injection", "564": "sql-injection",
    "77": "command-injection", "78": "command-injection", "88": "command-injection",
    "502": "unsafe-deserialization",
    "94": "code-eval", "95": "code-eval", "96": "code-eval",
    "79": "xss", "80": "xss", "116": "xss",
    "22": "path-traversal", "23": "path-traversal", "36": "path-traversal", "73": "path-traversal",
    "284": "broken-authz", "285": "broken-authz", "639": "broken-authz",
    "862": "broken-authz", "863": "broken-authz", "566": "broken-authz",
    "259": "hardcoded-secret", "321": "hardcoded-secret", "798": "hardcoded-secret",
    "326": "weak-crypto", "327": "weak-crypto", "328": "weak-crypto",
    "330": "weak-crypto", "338": "weak-crypto", "916": "weak-crypto",
    "918": "ssrf",
}

# Fallback when no CWE is present: substring of the tool's ruleId → class.
_SARIF_KEYWORD_TO_CLASS: tuple[tuple[str, str], ...] = (
    ("sqli", "sql-injection"), ("sql-injection", "sql-injection"), ("sql_injection", "sql-injection"),
    ("command-injection", "command-injection"), ("os-command", "command-injection"),
    ("os_command", "command-injection"), ("shell", "command-injection"), ("subprocess", "command-injection"),
    ("deserial", "unsafe-deserialization"), ("pickle", "unsafe-deserialization"),
    ("yaml-load", "unsafe-deserialization"), ("marshal", "unsafe-deserialization"),
    ("code-injection", "code-eval"), ("eval", "code-eval"), ("exec-used", "code-eval"),
    ("xss", "xss"), ("cross-site", "xss"),
    ("path-traversal", "path-traversal"), ("pathtraversal", "path-traversal"),
    ("path_traversal", "path-traversal"), ("directory-traversal", "path-traversal"),
    ("ssrf", "ssrf"),
    ("hardcoded", "hardcoded-secret"), ("secret", "hardcoded-secret"), ("password", "hardcoded-secret"),
    ("weak-crypto", "weak-crypto"), ("weak_crypto", "weak-crypto"), ("md5", "weak-crypto"),
    ("sha1", "weak-crypto"), ("insecure-hash", "weak-crypto"), ("insecure-random", "weak-crypto"),
    ("authz", "broken-authz"), ("authoriz", "broken-authz"), ("idor", "broken-authz"), ("bola", "broken-authz"),
)

_CWE_RE = re.compile(r"cwe[-_ ]?(\d+)", re.IGNORECASE)


def _sarif_flatten(value) -> list[str]:
    """Coerce a tag/property value (str, list, or nested) to a flat list of strs."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            out.extend(_sarif_flatten(v))
        return out
    return [str(value)]


def _sarif_cwe_numbers(texts: Iterable[str]) -> list[str]:
    nums: list[str] = []
    for t in texts:
        for m in _CWE_RE.finditer(t or ""):
            if m.group(1) not in nums:
                nums.append(m.group(1))
    return nums


def _sarif_class(rule_id: str, cwe_numbers: list[str]) -> str:
    for n in cwe_numbers:
        if n in _SARIF_CWE_TO_CLASS:
            return _SARIF_CWE_TO_CLASS[n]
    rid = (rule_id or "").lower()
    for needle, cls in _SARIF_KEYWORD_TO_CLASS:
        if needle in rid:
            return cls
    return "sast-other"


def _sarif_severity(result: dict, rule: dict) -> str:
    """SARIF security-severity (0-10) or level → our severity vocabulary."""
    for holder in (result.get("properties") or {}, rule.get("properties") or {}):
        raw = holder.get("security-severity")
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        if score >= 9.0:
            return "critical"
        if score >= 7.0:
            return "high"
        if score >= 4.0:
            return "medium"
        return "low"
    level = (result.get("level") or "").lower()
    return {"error": "high", "warning": "medium", "note": "low", "none": "low"}.get(level, "medium")


def _sarif_relpath(uri: str, root: Path) -> str:
    u = uri or ""
    if u.startswith("file://"):
        u = u[7:]
        if re.match(r"/[A-Za-z]:", u):     # file:///C:/... → strip the leading slash
            u = u[1:]
    u = u.replace("\\", "/")
    try:
        p = Path(u)
        if p.is_absolute():
            return p.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        pass
    return u[2:] if u.startswith("./") else u


def _sarif_location(result: dict) -> "tuple[str, int, str] | None":
    for loc in (result.get("locations") or []):
        if not isinstance(loc, dict):
            continue
        phys = loc.get("physicalLocation") or {}
        uri = (phys.get("artifactLocation") or {}).get("uri")
        region = phys.get("region") or {}
        line = region.get("startLine")
        if uri and isinstance(line, int):
            snippet = (region.get("snippet") or {}).get("text") or ""
            return uri, line, snippet
    return None


def _parse_sarif_file(path: Path, root: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    out: list[dict] = []
    for run in (data.get("runs") or []):
        if not isinstance(run, dict):
            continue
        rules = (((run.get("tool") or {}).get("driver") or {}).get("rules")) or []
        rules = [r for r in rules if isinstance(r, dict)]
        by_id = {r.get("id"): r for r in rules}
        for result in (run.get("results") or []):
            if not isinstance(result, dict):
                continue
            rule_ref = result.get("rule") or {}
            rule_id = result.get("ruleId") or rule_ref.get("id") or ""
            idx = result.get("ruleIndex")
            if idx is None:
                idx = rule_ref.get("index")
            rule = rules[idx] if isinstance(idx, int) and 0 <= idx < len(rules) else by_id.get(rule_id, {})
            props = rule.get("properties") or {}
            texts = [rule_id, rule.get("id", ""), rule.get("name", "")]
            texts += _sarif_flatten(props.get("tags"))
            texts += _sarif_flatten(props.get("cwe"))
            texts += [t.get("id", "") for t in (result.get("taxa") or []) if isinstance(t, dict)]
            cwes = _sarif_cwe_numbers(texts)
            cls = _sarif_class(rule_id, cwes)

            loc = _sarif_location(result)
            if not loc:
                continue
            uri, line, snip = loc
            rel = _sarif_relpath(uri, root)
            snippet = snip or (result.get("message") or {}).get("text") or rule_id
            snippet = _normalize(snippet)[:200]

            meta = CLASS_META[cls]
            if cls == "sast-other":
                cwe = f"CWE-{cwes[0]}" if cwes else meta.cwe
                severity = _sarif_severity(result, rule)
            else:
                cwe, severity = meta.cwe, meta.severity
            out.append({
                "id": candidate_id(rel, line, cls, snippet),
                "file": rel,
                "line": line,
                "class": cls,
                "cwe": cwe,
                "rule": meta.rule,
                "severity": severity,
                "confidence": "high",     # real SAST output is higher-signal than a regex tripwire
                "snippet": snippet,
                "source": "sarif",
            })
    return out


def _find_sarif(root: Path) -> list[Path]:
    """Bounded, skip-dir-respecting walk for *.sarif files under *root*."""
    out: list[Path] = []
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
            except OSError:
                continue
            if entry.suffix.lower() == ".sarif":
                out.append(entry)
    return sorted(out)


def ingest_sarif(root: "str | Path", sarif_paths: "list | None" = None) -> list[dict]:
    """Parse SAST output (*.sarif) into native-shaped candidates. Returns [] when
    disabled, when no SARIF is present, or on any error — never raises.

    sarif_paths=None auto-detects every *.sarif under *root*; a list of file/dir
    paths ingests exactly those (the `--sarif` opt-in)."""
    if scan_disabled():
        return []
    try:
        root = Path(root).resolve()
    except OSError:
        return []
    if sarif_paths is None:
        files = _find_sarif(root) if root.is_dir() else []
    else:
        files = []
        for sp in sarif_paths:
            try:
                spp = Path(sp)
                if spp.is_dir():
                    files.extend(_find_sarif(spp))
                elif spp.is_file():
                    files.append(spp)
            except OSError:
                continue
    out: list[dict] = []
    seen_ids: set[str] = set()
    for f in files:
        try:
            cands = _parse_sarif_file(f, root)
        except Exception:
            continue          # a malformed .sarif is skipped, not fatal
        for c in cands:
            if c["id"] in seen_ids:
                continue
            seen_ids.add(c["id"])
            out.append(c)
    return out


def enumerate_candidates(root: "str | Path", sarif: "list | bool | None" = None) -> list[dict]:
    """Enumerate every attack-surface candidate under *root*, deterministically
    sorted (file, line, class). Returns [] when disabled or on any fatal error —
    it never raises.

    Each candidate: ``{id, file, line, class, cwe, rule, severity, confidence,
    snippet}`` (SARIF-sourced candidates also carry ``source: "sarif"``). At most
    one candidate per (file, line, class): the first pattern of a class to match a
    line wins, so two SQL patterns on one line don't double count — and a native
    hit and an ingested SARIF hit on the same spot collapse to the native one.

    ``sarif``: None auto-detects *.sarif under root; a list ingests those explicit
    paths; False skips SARIF entirely.
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

    # Fold in ingested SAST output through the SAME (file,line,class) dedup, so a
    # native tripwire hit already recorded wins over a SARIF hit on the same spot.
    if sarif is not False:
        for cand in ingest_sarif(root, None if sarif is None else sarif):
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
