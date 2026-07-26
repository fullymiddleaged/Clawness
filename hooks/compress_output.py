#!/usr/bin/env python3
"""
PostToolUse hook for Bash — compresses verbose tool output before
Claude's next turn sees it.

How it works:
  - Fires after every Bash tool call
  - If output is short (<80 lines), does nothing
  - If output is long, drops noise, extracts errors/failures, and
    replaces the result with a compressed summary via updatedToolOutput
  - Claude sees the summary instead of wading through noise

Configure in settings.json under hooks.PostToolUse with matcher "Bash".
"""

from __future__ import annotations

import io
import json
import re
import sys

# The payload is UTF-8 (Claude Code is Node; JSON.stringify emits raw UTF-8, not
# \uXXXX escapes). Without this, stdin decodes as cp1252 on Windows and every
# em-dash/smart-quote in the output round-trips back to Claude as mojibake
# ("—" → "â€""). stdout matters too: the compressed block is re-encoded on the
# way out. Same pin as every other stdin-reading hook.
# isinstance narrows to the class that actually defines reconfigure() — sys.stdin
# is typed TextIO, which doesn't — and skips an already-replaced stream.
for _stream in (sys.stdin, sys.stdout):
    if isinstance(_stream, io.TextIOWrapper):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


# Lines below this threshold pass through unchanged
SHORT_THRESHOLD = 80

# Max lines in the compressed output
MAX_COMPRESSED = 40

# Patterns that indicate errors/failures — always kept.
# Note: no bare "warn" here — it matched benign "npm warn" noise and
# promoted it into the errors section. Use specific, bounded patterns.
ERROR_PATTERNS = re.compile(
    r"(?i)(\berror\b|\bfailed\b|\bfailure\b|exception|traceback|panic|"
    r"\bfatal\b|\bERR!\b|errno|✗|✖|ENOENT|EACCES|EPERM|SyntaxError|"
    r"TypeError|ReferenceError|ModuleNotFoundError|"
    r"cannot find|not found|permission denied|"
    r"build error|compile error|type error|"
    r"\bassert\b|assertion)",
)

# Patterns that are pure noise — dropped before anything else is considered.
NOISE_PATTERNS = re.compile(
    r"(?i)(^\s*$|^npm warn|^npm notice|"
    r"^downloading |^installing |"
    r"^\s*[\-=]{10,}\s*$|"          # separator lines
    r"^\s*\d+\s+passing\b|"          # "42 passing" summary
    r"^[\s│├└─┬┤]*$)",              # tree-drawing characters only
)

# Commands whose output IS the payload, not progress noise: the user/model asked
# for file or diff CONTENT and will reason about it line by line. Dropping the
# middle of a file is silently lossy in a way build noise never is — Claude acts
# on a mangled view and can't tell what it lost. These skip noise-stripping and
# error-extraction entirely (see _truncate_content): blank lines are structure
# here, not noise, and there is no "error section" to hoist out of a source file.
CONTENT_COMMANDS = re.compile(
    r"(?i)(^|[|;&]\s*)(cat|bat|head|tail|sed\s+-n|nl|jq|"
    r"grep|egrep|fgrep|rg|ag|"
    r"git\s+show|git\s+diff|git\s+blame|type)\b",
)

# Verbatim lines kept for a content read before truncation kicks in. Generous on
# purpose: this is content that was explicitly asked for.
CONTENT_HEAD_LIMIT = 400

# Commands known to be verbose — get extra compression
VERBOSE_COMMANDS = re.compile(
    r"(?i)(npm test|npm run|npx jest|npx vitest|pytest|"
    r"npx next build|npx tsc|eslint|cargo test|cargo build|"
    r"go test|make |gradle |mvn |pip install|"
    r"git log(?!\s+--oneline))",   # git diff/show are CONTENT_COMMANDS, handled first
)


def _truncate_content(lines: list[str]) -> str | None:
    """Compression for a content read: keep the head VERBATIM (blank lines and
    all) and, if it overflows, say plainly what is missing and how to get it.

    Head-plus-tail with the middle silently gone is the dangerous shape — the
    model can't tell which lines it lost or recover them. An explicit, honest
    truncation is safe: Claude knows exactly what it's missing and that Read/Grep
    will fetch it."""
    if len(lines) <= CONTENT_HEAD_LIMIT:
        return None  # short enough — pass the content through untouched
    omitted = len(lines) - CONTENT_HEAD_LIMIT
    return "\n".join([
        *lines[:CONTENT_HEAD_LIMIT],
        "",
        f"[clawness: content read truncated — {omitted} of {len(lines)} lines "
        f"omitted after the first {CONTENT_HEAD_LIMIT}. Nothing was dropped from "
        f"the text above. Use Read (with offset/limit) or Grep to get the rest.]",
    ])


def compress(output: str, command: str) -> str | None:
    """
    Compress long tool output. Returns compressed string or None if
    the output is short enough to pass through unchanged.
    """
    raw_lines = output.splitlines()

    if len(raw_lines) <= SHORT_THRESHOLD:
        return None

    # A content read is truncated honestly, never gutted. Checked BEFORE the
    # noise/error passes: those are built for build logs and actively destroy
    # source (blank lines stripped, middle discarded, "error" matching a mere
    # mention of the word in prose).
    if CONTENT_COMMANDS.search(command):
        return _truncate_content(raw_lines)

    # Drop pure-noise lines up front so head/tail/error context is signal,
    # not npm-warn spam and separator bars.
    lines = [ln for ln in raw_lines if not NOISE_PATTERNS.search(ln)]
    if not lines:
        lines = raw_lines  # everything was "noise" — keep something to show

    is_verbose_cmd = bool(VERBOSE_COMMANDS.search(command))

    # Phase 1: extract error lines with surrounding context. Track line INDICES,
    # not text: a text-membership dedup drops distinct lines that happen to
    # repeat, and only indices can tell which kept lines overlap head/tail
    # (the old "kept" count double-counted those).
    error_idx_all: list[int] = []
    seen: set[int] = set()
    context_radius = 2

    for i, line in enumerate(lines):
        if ERROR_PATTERNS.search(line):
            start = max(0, i - context_radius)
            end = min(len(lines), i + context_radius + 1)
            for j in range(start, end):
                if j not in seen:
                    seen.add(j)
                    error_idx_all.append(j)
    error_idx = error_idx_all[:MAX_COMPRESSED]

    # Phase 2: keep first few and last few lines for context
    head_idx = list(range(min(5, len(lines))))
    tail_idx = list(range(max(0, len(lines) - 5), len(lines)))
    show_tail = tail_idx != head_idx

    # Phase 3: build compressed output
    body: list[str] = []
    if head_idx:
        body.append("--- start ---")
        body.extend(lines[j] for j in head_idx)

    if error_idx:
        body.append("")
        body.append(f"--- errors/warnings ({len(error_idx_all)} lines) ---")
        body.extend(lines[j] for j in error_idx)
        if len(error_idx_all) > MAX_COMPRESSED:
            body.append(f"  ... {len(error_idx_all) - MAX_COMPRESSED} more error lines truncated")
    elif is_verbose_cmd:
        body.append("")
        body.append("--- no errors detected ---")

    if show_tail:
        body.append("")
        body.append("--- end ---")
        body.extend(lines[j] for j in tail_idx)

    # Count distinct output lines actually shown (headers excluded) — a line
    # sitting in both the head and an error window counts once.
    kept_set = set(head_idx) | set(error_idx)
    if show_tail:
        kept_set |= set(tail_idx)
    kept = len(kept_set)

    parts = [
        f"[clawness: compressed {len(raw_lines)} lines → {kept} kept "
        f"({len(raw_lines) - len(lines)} noise lines dropped)]",
        "",
    ]
    parts.extend(body)
    return "\n".join(parts)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, IOError):
        sys.exit(0)

    tool_name = event.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    command = ""
    tool_input = event.get("tool_input", {})
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")

    # Keep the original response object so we can return a replacement that
    # matches the tool's output shape (Claude Code requires this).
    raw_response = event.get("tool_response", "")
    if isinstance(raw_response, dict):
        combined = str(raw_response.get("stdout", "")) + str(raw_response.get("stderr", ""))
    elif isinstance(raw_response, str):
        combined = raw_response
    else:
        combined = str(raw_response)

    compressed = compress(combined, command)

    if compressed is None:
        # Short output — pass through unchanged.
        sys.exit(0)

    # Replace the tool result Claude sees. `updatedToolOutput` substitutes the
    # output (additionalContext would only *append*, leaving the full verbose
    # result in context and defeating the purpose).
    #
    # The replacement MUST match the tool's output shape. Bash returns an
    # object {stdout, stderr, interrupted, isImage, ...}; we preserve every
    # original field and only swap stdout (clearing stderr, since it's folded
    # into the compressed view). If the response was a bare string, we return
    # a string. Replacing built-in tool output requires Claude Code >= 2.1.121.
    if isinstance(raw_response, dict):
        updated = dict(raw_response)
        updated["stdout"] = compressed
        updated["stderr"] = ""
        updated_output = updated
    else:
        updated_output = compressed

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": updated_output,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
