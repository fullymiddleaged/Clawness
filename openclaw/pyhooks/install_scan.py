#!/usr/bin/env python3
"""
install_scan.py — OpenClaw-only: scan an about-to-be-installed artifact for
prompt-injection / exfil tells and emit findings the adapter maps onto
OpenClaw's `before_install` result (`{findings, block, blockReason}`).

This is the OpenClaw-native home for Clawness's trust vetting: Claude Code has no
install-time hook, so this path exists ONLY for OpenClaw. It reuses
`clawness.trust.scan_injection_tells` read-only — it changes nothing in the shared
engine, and Claude Code never runs it.

Contract: JSON on stdin `{ "sourcePath": "<path>" }`; JSON on stdout
`{ "findings": [{ruleId, severity, file, line, message}], "critical": <int> }`.
Fails toward an empty result (no findings, no block) on any error — an install must
never be blocked by a broken scanner.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

# Pin UTF-8 stdio before any read/print (Windows defaults to cp1252 and would
# mangle non-ASCII paths or the JSON payload). Mirrors hooks/_hookutil.py.
for _stream in (sys.stdin, sys.stdout):
    if isinstance(_stream, io.TextIOWrapper):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# openclaw/pyhooks/install_scan.py → parents[2] is the repo root holding clawness/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from clawness.trust import scan_injection_tells
except Exception:  # engine not importable — fail toward doing nothing
    scan_injection_tells = None  # type: ignore[assignment]

# Labels (substrings) that mean the artifact is trying to hijack the agent or
# exfiltrate — near-zero legitimate use inside a skill/plugin body, so they arm a
# block. The dual-use tells (curl, .env, base64, zero-width) stay advisory: a real
# security skill legitimately mentions them, so they warn but never block.
_CRITICAL_MARKERS = (
    "instruction override",
    "persona hijack",
    "redefine the system prompt",
    "concealment phrasing",
    "webhook/paste exfil",
    "instance-metadata",
    "decode-and-execute",
)

# Bound the walk so a large package can't stall an install. Skip vendored/build
# dirs (they aren't the authored artifact) and oversized/binary files.
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv"}
_MAX_FILES = 3000
_MAX_FILE_BYTES = 1_000_000


def _is_critical(label: str) -> bool:
    low = label.lower()
    return any(m in low for m in _CRITICAL_MARKERS)


def _iter_files(root: Path):
    """Yield files under *root* (or root itself if it's a file), bounded."""
    if root.is_file():
        yield root
        return
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= _MAX_FILES:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        count += 1
        yield path


def _scan_file(path: Path, root: Path) -> list[dict]:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    try:
        rel = str(path.relative_to(root)) if root.is_dir() else path.name
    except ValueError:
        rel = str(path)

    findings: list[dict] = []
    # Line-by-line so each finding carries a real line number for the host UI.
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label in scan_injection_tells(line):
            findings.append(
                {
                    "ruleId": "clawness/injection-tell",
                    "severity": "critical" if _is_critical(label) else "warn",
                    "file": rel,
                    "line": lineno,
                    "message": f"Injection/exfil tell: {label}",
                }
            )
    return findings


def main() -> None:
    result = {"findings": [], "critical": 0}
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        source = payload.get("sourcePath") or payload.get("source_path") or ""
        if scan_injection_tells is None or not source:
            print(json.dumps(result))
            return
        root = Path(source)
        if not root.exists():
            print(json.dumps(result))
            return

        findings: list[dict] = []
        for path in _iter_files(root):
            findings.extend(_scan_file(path, root))
            if len(findings) >= 200:  # cap the report; the signal is already made
                break

        result["findings"] = findings
        result["critical"] = sum(1 for f in findings if f["severity"] == "critical")
    except Exception:
        # Any failure → empty result. An install is never blocked by a crash here.
        result = {"findings": [], "critical": 0}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
