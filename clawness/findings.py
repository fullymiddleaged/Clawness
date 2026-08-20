"""
Findings + coverage ledger — the memory half of the security audit.

``scan.py`` enumerates candidates deterministically but statelessly: every run
returns the same list, with no notion of "I already judged this one." This module
persists verdicts to ``.clawness/security/findings.json`` and merges each new scan
into it, so runs **accumulate** instead of repeating. That is what replaces "scan
10 times and hope": you adjudicate only what is `new`, and a coverage signal tells
you when there is nothing left to look at.

The state machine per candidate id:

    new ──adjudicate──▶ reviewed / confirmed / false-positive / fixed
     │                          │
     └── disappears ──▶ gone ◀──┘   (a sink removed from the code)
                         │
                         └── reappears ──▶ prior adjudication (or new)

A human verdict is **never silently discarded**: when a sink disappears the entry
becomes ``gone`` but remembers its adjudication, so a sink that comes back is not
re-opened as `new`. Writes are atomic (``atomic_write_text``) so two concurrent
sessions never read a torn ledger.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .plan import atomic_write_text, clawness_dir

STATUS_NEW = "new"
STATUS_REVIEWED = "reviewed"
STATUS_CONFIRMED = "confirmed"
STATUS_FALSE_POSITIVE = "false-positive"
STATUS_FIXED = "fixed"
STATUS_GONE = "gone"

# The four statuses that represent a human/LLM judgment having been made.
ADJUDICATED = frozenset({STATUS_REVIEWED, STATUS_CONFIRMED, STATUS_FALSE_POSITIVE, STATUS_FIXED})
VALID_STATUSES = ADJUDICATED | {STATUS_NEW, STATUS_GONE}

# Fields copied from a scan candidate onto its ledger entry, so the ledger is
# self-describing (no need to re-run the scan to read a finding).
_CANDIDATE_FIELDS = ("file", "line", "class", "cwe", "rule", "severity", "confidence", "snippet")


def findings_path(root: Path) -> Path:
    return clawness_dir(root) / "security" / "findings.json"


def load_findings(root: Path) -> dict:
    """Load the ledger ({id: entry}). Returns {} on any error / missing file."""
    try:
        data = json.loads(findings_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Tolerate a wrapped shape ({"findings": {...}}) as well as the bare map.
    if "findings" in data and isinstance(data["findings"], dict):
        data = data["findings"]
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save_findings(root: Path, ledger: dict) -> None:
    atomic_write_text(findings_path(root), json.dumps(ledger, indent=2, sort_keys=True) + "\n")


def merge_scan(candidates: list[dict], ledger: dict, now: "float | None" = None) -> dict:
    """Merge a fresh candidate list into *ledger*, returning a NEW ledger dict.

    - A candidate whose id is unseen is added with status ``new``.
    - A candidate already present refreshes ``last_seen`` and its location/snippet;
      its status (and any adjudication) is preserved.
    - A ledger id absent from this scan is marked ``gone`` — remembering its prior
      adjudication so a later reappearance restores it rather than re-opening it.
    - A ``gone`` entry whose sink reappears is restored to its prior adjudicated
      status, or ``new`` if it never had one.

    Pure: it neither reads nor writes disk, so it is trivially testable.
    """
    now = time.time() if now is None else now
    out: dict = {k: dict(v) for k, v in ledger.items() if isinstance(v, dict)}
    seen_ids = set()

    for cand in candidates:
        cid = cand.get("id")
        if not cid:
            continue
        seen_ids.add(cid)
        entry = out.get(cid)
        if entry is None:
            entry = {f: cand.get(f) for f in _CANDIDATE_FIELDS}
            entry.update({
                "status": STATUS_NEW,
                "verdict": "",
                "notes": "",
                "first_seen": now,
                "last_seen": now,
            })
            out[cid] = entry
            continue
        # Existing entry: refresh volatile fields, preserve judgment.
        for f in _CANDIDATE_FIELDS:
            if cand.get(f) is not None:
                entry[f] = cand.get(f)
        entry["last_seen"] = now
        if entry.get("status") == STATUS_GONE:
            entry["status"] = entry.pop("_adjudicated", None) or STATUS_NEW

    # Mark anything not in this scan as gone (remembering an adjudication).
    for cid, entry in out.items():
        if cid in seen_ids:
            continue
        if entry.get("status") == STATUS_GONE:
            continue
        if entry.get("status") in ADJUDICATED:
            entry["_adjudicated"] = entry["status"]
        entry["status"] = STATUS_GONE
        entry["last_seen"] = entry.get("last_seen", now)

    return out


def set_verdict(
    ledger: dict,
    finding_id: str,
    status: str,
    verdict: "str | None" = None,
    severity: "str | None" = None,
    notes: "str | None" = None,
) -> dict:
    """Record an adjudication on one finding, returning a NEW ledger. Raises
    ValueError on an unknown status or id — the caller (CLI/agent) wrote it, so a
    typo should be loud, not silently dropped."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
    if finding_id not in ledger:
        raise ValueError(f"unknown finding id {finding_id!r}")
    out = {k: dict(v) for k, v in ledger.items()}
    entry = out[finding_id]
    entry["status"] = status
    if verdict is not None:
        entry["verdict"] = verdict
    if severity is not None:
        entry["severity"] = severity
    if notes is not None:
        entry["notes"] = notes
    entry.pop("_adjudicated", None)   # an explicit verdict clears the gone-memory
    return out


def outstanding(ledger: dict) -> list[dict]:
    """Live candidates still awaiting adjudication (status ``new``), each with its
    id folded in — the exact list an audit pass should look at, nothing already
    judged and nothing gone."""
    out = []
    for cid, entry in ledger.items():
        if isinstance(entry, dict) and entry.get("status") == STATUS_NEW:
            out.append({"id": cid, **entry})
    out.sort(key=lambda c: (c.get("file", ""), c.get("line", 0)))
    return out


def coverage(ledger: dict) -> dict:
    """Adjudication coverage over the LIVE (non-gone) candidate set.

    ``converged`` is True when no live candidate is still ``new`` — i.e. every
    outstanding sink has been looked at, which is the "you can stop scanning"
    signal. An empty ledger counts as converged (nothing to judge).
    """
    counts = {s: 0 for s in VALID_STATUSES}
    for entry in ledger.values():
        if not isinstance(entry, dict):
            continue
        st = entry.get("status")
        if st in counts:
            counts[st] += 1

    live = sum(v for s, v in counts.items() if s != STATUS_GONE)
    adjudicated = sum(counts[s] for s in ADJUDICATED)
    new = counts[STATUS_NEW]
    pct = 100.0 if live == 0 else round(100.0 * adjudicated / live, 1)
    return {
        "total": sum(counts.values()),
        "live": live,
        "gone": counts[STATUS_GONE],
        "adjudicated": adjudicated,
        "outstanding": new,
        "confirmed": counts[STATUS_CONFIRMED],
        "false_positive": counts[STATUS_FALSE_POSITIVE],
        "fixed": counts[STATUS_FIXED],
        "reviewed": counts[STATUS_REVIEWED],
        "pct": pct,
        "converged": new == 0,
    }
