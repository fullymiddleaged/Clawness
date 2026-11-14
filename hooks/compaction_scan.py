#!/usr/bin/env python3
"""
Clawness — compaction history scan (SessionStart, async).

Tops up `<config>/clawness/compaction.json` with the hard-limit compactions
recorded in this user's transcripts, so the context watch knows where each model
actually compacts (see clawness/compaction.py). Registered `async`, so it never
delays a session opening; the watch reads whatever cache exists on the next
prompt and falls back to its defaults until then.

Only bytes appended since the last scan are read, so after the first run this
costs a few milliseconds. Produces no output and always exits 0. Opt out with
CLAW_NO_CONTEXT_WATCH=1 (the watch it feeds).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hookutil import read_payload  # noqa: E402


def main() -> None:
    try:
        read_payload()  # drain stdin; the payload itself isn't needed
        if os.environ.get("CLAW_NO_CONTEXT_WATCH"):
            return
        from clawness.compaction import scan_history
        scan_history()
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
