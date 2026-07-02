"""
Tests for the PostToolUse output-compression hook (hooks/compress_output.py).

Runs under pytest, or standalone:  python tests/test_compress.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import compress_output as C  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("done")
