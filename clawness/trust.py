"""
Trust ledger — trust-on-first-use (TOFU) integrity for the agent's own context.

Skills, sub-agents, slash-commands and MCP servers are markdown/JSON that gets
loaded straight into the model's context — a prime prompt-injection / supply-chain
vector. This module fingerprints those artifacts so the SessionStart hook can
surface anything *new or changed* since last session (``hooks/trust_ledger.py``),
and so ``clawness audit-skills`` can scan their bodies for injection tells.

Pure logic, stdlib only (``hashlib``). No network, no third-party deps.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# Where Claude Code looks for project-scoped artifacts.
_AGENT_GLOB = ".claude/agents/*.md"
_SKILL_GLOB = ".claude/skills/*/SKILL.md"
_COMMAND_GLOBS = (".claude/commands/*.md", ".claude/commands/**/*.md")
_MCP_FILES = (".mcp.json",)
_SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> "str | None":
    try:
        return _sha256_bytes(path.read_bytes())
    except (OSError, ValueError):
        return None


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scan_artifacts(root: "str | Path") -> dict[str, str]:
    """Map each context-injected artifact under *root* to a content hash.

    Covers project ``.claude/`` agents, skills, commands, and MCP server
    definitions (from ``.mcp.json`` and the ``mcpServers`` block of
    ``.claude/settings*.json``). Keys are repo-relative paths (MCP-server keys get
    a ``#mcpServers`` suffix). Missing files are simply absent from the map.
    """
    root = Path(root)
    out: dict[str, str] = {}

    paths: list[Path] = []
    paths += sorted(root.glob(_AGENT_GLOB))
    paths += sorted(root.glob(_SKILL_GLOB))
    for g in _COMMAND_GLOBS:
        paths += sorted(root.glob(g))
    for name in _MCP_FILES:
        p = root / name
        if p.is_file():
            paths.append(p)

    seen: set[Path] = set()
    for p in paths:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        h = _hash_file(p)
        if h is not None:
            out[_rel(p, root)] = h

    # MCP servers declared inside settings files: fingerprint just that block so
    # unrelated settings churn doesn't trip the ledger.
    for name in _SETTINGS_FILES:
        p = root / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if servers:
            blob = json.dumps(servers, sort_keys=True).encode("utf-8")
            out[f"{_rel(p, root)}#mcpServers"] = _sha256_bytes(blob)

    return out


def diff_ledger(old: dict[str, str], new: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Return (added, changed, removed) artifact keys between two scans."""
    old = old or {}
    new = new or {}
    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = sorted(k for k in new if k in old and new[k] != old[k])
    return added, changed, removed


# --- injection-tell scanner (for `clawness audit-skills`) -----------------
_INJECTION_TELLS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("instruction override ('ignore previous')",
     re.compile(r"(?i)\bignore\s+(all\s+|any\s+)?previous\b")),
    ("instruction override ('disregard the above')",
     re.compile(r"(?i)\bdisregard\s+(the\s+|all\s+)?(above|previous|prior|earlier)\b")),
    ("persona hijack ('you are now')",
     re.compile(r"(?i)\byou are now\b")),
    ("claims to redefine the system prompt",
     re.compile(r"(?i)\b(system prompt|new instructions|override (your|the) (rules|instructions))\b")),
    ("embedded network downloader",
     re.compile(r"(?i)\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod)\b")),
    ("references credential/secret material",
     re.compile(r"(?i)(\.env\b|\bid_rsa\b|/\.ssh/|/\.aws/|AWS_SECRET|SECRET_KEY|PRIVATE[_ ]?KEY)")),
    ("cloud instance-metadata endpoint",
     re.compile(r"169\.254\.169\.254|fd00:ec2::254|metadata\.google\.internal|"
                r"metadata\.azure\.com|100\.100\.100\.200")),
    ("long base64 blob (possible hidden payload)",
     re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")),
)


def scan_injection_tells(text: str) -> list[str]:
    """Return human-readable labels for injection/exfil patterns found in *text*.

    High-signal but advisory: a hit warrants a human look, not an automatic
    verdict (a security skill may legitimately mention `curl` or `.env`)."""
    if not text:
        return []
    return [label for label, rx in _INJECTION_TELLS if rx.search(text)]
