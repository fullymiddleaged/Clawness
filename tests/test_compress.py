"""
Tests for the PostToolUse output-compression hook (hooks/compress_output.py).

Runs under pytest, or standalone:  python tests/test_compress.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import compress_output as C  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "compress_output.py"


def _kept(block: str) -> int:
    m = re.search(r"→ (\d+) kept", block)
    assert m, f"no kept count in header: {block.splitlines()[0]}"
    return int(m.group(1))


def test_short_output_passes_through():
    assert C.compress("\n".join(f"line {i}" for i in range(20)), "pytest") is None


def test_kept_count_matches_distinct_lines_when_error_overlaps_head():
    # An error inside the first 5 lines makes its context window overlap the
    # head section — the count must not tally those lines twice.
    lines = [f"line {i}" for i in range(100)]
    lines[2] = "line 2 has an error in it"
    out = C.compress("\n".join(lines), "make build")
    assert out is not None
    # head 0-4 and the error window 0-4 are the same 5 lines; tail is 5 more.
    assert _kept(out) == 10


def test_distinct_duplicate_lines_are_all_kept():
    # Two identical error lines are distinct output lines — the old text-based
    # dedup silently dropped the second occurrence.
    lines = [f"filler {i}" for i in range(100)]
    lines[50] = "assertion failed: x == y"
    lines[51] = "assertion failed: x == y"
    out = C.compress("\n".join(lines), "pytest")
    assert out is not None
    assert out.count("assertion failed: x == y") == 2


def test_verbose_command_with_no_errors_says_so():
    lines = [f"ok {i}" for i in range(100)]
    out = C.compress("\n".join(lines), "pytest -q")
    assert out is not None
    assert "no errors detected" in out
    assert _kept(out) == 10  # head 5 + tail 5, disjoint


def test_content_read_is_not_gutted():
    """A `cat` of source is content, not build noise: it must survive whole, with
    blank lines (markdown/code structure) intact. The head+tail+error shape used
    to cut 137 lines of SKILL.md down to 15."""
    lines = [f"content line {i}" if i % 7 else "" for i in range(137)]
    out = C.compress("\n".join(lines), "cat skills/security-audit/SKILL.md skills/review/SKILL.md")
    assert out is None, "content reads under the limit must pass through untouched"


def test_content_read_over_limit_truncates_honestly():
    """Past the limit, keep the head verbatim and SAY what's missing — never drop
    the middle silently."""
    lines = [f"line {i}" for i in range(C.CONTENT_HEAD_LIMIT + 50)]
    out = C.compress("\n".join(lines), "cat big_file.py")
    assert out is not None
    assert "line 0" in out and f"line {C.CONTENT_HEAD_LIMIT - 1}" in out
    assert "50 of 450 lines omitted" in out
    assert "Read" in out  # points at the way to recover the rest
    # the tail must NOT be spliced on — that's the shape that hides the gap
    assert f"line {C.CONTENT_HEAD_LIMIT + 49}" not in out


def test_content_read_keeps_words_that_look_like_errors():
    """Prose mentioning "error" in a source file must not be hoisted into an
    "errors/warnings" section — it isn't one."""
    lines = [f"line {i}" for i in range(100)]
    lines[50] = "# Handle the error case gracefully"
    out = C.compress("\n".join(lines), "git diff HEAD~1")
    assert out is None
    assert not C.CONTENT_COMMANDS.search("pytest -q")  # build cmds still compress


def test_build_output_still_compresses():
    """The bypass must not disarm the hook's actual job."""
    out = C.compress("\n".join(f"ok {i}" for i in range(200)), "npm run build")
    assert out is not None and "clawness: compressed" in out


def test_hook_roundtrips_utf8_payload():
    """End-to-end: Node's JSON.stringify sends RAW UTF-8. Reading stdin with the
    platform default (cp1252 on Windows) turned every em-dash in the output into
    mojibake on the way back to Claude."""
    body = ["3. **Report** — Show what was tested"] + [f"x {i}" for i in range(200)]
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "npm run build"},
        "tool_response": {"stdout": "\n".join(body), "stderr": ""},
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        # ensure_ascii=False mirrors JSON.stringify: raw UTF-8, no \uXXXX escapes
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    got = json.loads(proc.stdout.decode("utf-8"))
    text = got["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert "—" in text, "em-dash was mangled in transit"
    assert "â€" not in text, "classic UTF-8-read-as-cp1252 mojibake"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("done")
