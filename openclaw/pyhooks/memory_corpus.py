#!/usr/bin/env python3
"""
memory_corpus.py — OpenClaw-only: expose `.clawness/memory.md` project lessons as
a native, searchable OpenClaw memory corpus (via `registerMemoryCorpusSupplement`).

Additive and non-displacing: Claude Code still injects the ranked memory block every
turn through `before_prompt_build`; this ONLY makes the same lessons discoverable
through OpenClaw's native memory search, so the model can pull a relevant lesson on
demand. It reuses `clawness.memory` read-only and never touches the shared engine.

Contract: JSON on stdin. `mode` selects the operation:
  search: { mode:"search", cwd, query, maxResults? }
          → [{corpus, path, title, score, snippet, id, sourceType, sourcePath}]
  get:    { mode:"get", cwd, lookup }
          → {corpus, path, title, content, fromLine, lineCount, id} | null
Fails toward empty ([] / null) on any error.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

for _stream in (sys.stdin, sys.stdout):
    if isinstance(_stream, io.TextIOWrapper):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# openclaw/pyhooks/memory_corpus.py → parents[2] is the repo root holding clawness/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from clawness.memory import parse_memory, rank_lessons, read_memory
except Exception:
    parse_memory = rank_lessons = read_memory = None  # type: ignore[assignment]

_CORPUS = "clawness-memory"
_REL_PATH = ".clawness/memory.md"
_DEFAULT_MAX = 8


def _memory_path(cwd: str) -> Path:
    return Path(cwd or ".") / ".clawness" / "memory.md"


def _flatten(entry: str) -> str:
    """One-line form of a possibly multi-line bullet, for snippet/title use."""
    return " ".join(part.strip() for part in entry.splitlines() if part.strip())


def _entry_id(index: int, text: str) -> str:
    # Stable-ish id: position plus a short hash of the text, so re-ordering the
    # file doesn't silently reuse an id for a different lesson.
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"clawness-memory:{index}:{digest}"


def _load(cwd: str) -> tuple[list[str], list[str], str]:
    path = _memory_path(cwd)
    raw = read_memory(path)  # returns "" when absent/unreadable
    if not raw.strip():
        return [], [], ""
    pinned, lessons = parse_memory(raw)
    return pinned, lessons, str(path)


def _do_search(payload: dict) -> list[dict]:
    query = str(payload.get("query") or "").strip()
    if not query:
        return []
    max_results = int(payload.get("maxResults") or _DEFAULT_MAX)
    if max_results <= 0:
        return []

    pinned, lessons, abspath = _load(str(payload.get("cwd") or ""))
    if not pinned and not lessons:
        return []

    # Pinned entries are always relevant (they're the "## Always" block); rank the
    # rest against the query. Pinned lead at score 1.0; matched lessons follow.
    ranked = rank_lessons(lessons, query, top_k=max_results) if lessons else []

    results: list[dict] = []
    order = [("pinned", e) for e in pinned] + [("lesson", e) for e in ranked]
    for i, (kind, entry) in enumerate(order[:max_results]):
        flat = _flatten(entry)
        score = 1.0 if kind == "pinned" else max(0.1, 0.9 - 0.01 * i)
        results.append(
            {
                "corpus": _CORPUS,
                "path": _REL_PATH,
                "title": (flat[:60] + "…") if len(flat) > 60 else flat,
                "score": round(score, 4),
                "snippet": flat[:280],
                "id": _entry_id(i, entry),
                "kind": kind,
                "sourceType": "clawness",
                "sourcePath": abspath,
            }
        )
    return results


def _do_get(payload: dict) -> "dict | None":
    lookup = str(payload.get("lookup") or "").strip()
    pinned, lessons, abspath = _load(str(payload.get("cwd") or ""))
    entries = pinned + lessons
    if not entries:
        return None

    # Match by our own id (index prefix) when given one, else by snippet substring.
    chosen: "str | None" = None
    if lookup.startswith("clawness-memory:"):
        try:
            idx = int(lookup.split(":")[1])
            if 0 <= idx < len(entries):
                chosen = entries[idx]
        except (IndexError, ValueError):
            chosen = None
    if chosen is None:
        low = lookup.lower()
        for entry in entries:
            if low and low in _flatten(entry).lower():
                chosen = entry
                break
    if chosen is None:
        return None

    return {
        "corpus": _CORPUS,
        "path": _REL_PATH,
        "title": _flatten(chosen)[:60],
        "content": chosen,
        "fromLine": 1,
        "lineCount": len(chosen.splitlines()) or 1,
        "id": _entry_id(entries.index(chosen), chosen),
        "sourceType": "clawness",
        "sourcePath": abspath,
    }


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if parse_memory is None:
            print(json.dumps(None if payload.get("mode") == "get" else []))
            return
        mode = payload.get("mode", "search")
        if mode == "get":
            print(json.dumps(_do_get(payload)))
        else:
            print(json.dumps(_do_search(payload)))
    except Exception:
        # Emit a valid empty result for whichever mode was (attempted).
        print(json.dumps([]))


if __name__ == "__main__":
    main()
