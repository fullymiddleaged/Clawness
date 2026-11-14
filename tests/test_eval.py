"""Retrieval-quality regression test: runs the real `clawness eval` over the
labeled ground-truth set with the same floors CI uses.

It used to re-implement the scoring loop here, which drifted from the CLI: it
ignored each case's `stack`, and broke outright on the `reject` and
`expect: []` cases added in 1.22.0. Driving the CLI keeps one definition of
"retrieval is good enough".
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FLOORS = ("--floor-mrr", "0.85", "--floor-hit", "0.95", "--floor-acc1", "0.97")


def test_retrieval_quality_floors():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "clawness.cli", "--rules-dir", str(ROOT / "rules"),
         "eval", *FLOORS],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "accuracy@1" in r.stdout
