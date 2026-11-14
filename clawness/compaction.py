"""
Learn where Claude Code actually compacts, from the user's own transcripts.

The context watch used to warn at a percentage of the model's window, which
stopped working once the big models went to a native 1M window: Claude Code
compacts long before 1M — at its limit (~967k) only in theory, and in practice
on the first message after a break once a session is past the idle-compaction
floor (`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`, 200k by default). Every
compaction leaves a `compact_boundary` record in the transcript carrying the
token count it fired at, so the user's history says where their sessions
really compact. This module reads that history into a small cache; the context
watch reads the cache.

Only HARD-LIMIT compactions are learned: `trigger == "auto"` (a manual
`/compact` says nothing about limits) with no long idle gap before it (an idle
compaction fires at whatever size the session happened to be, 201k-525k in the
history this was built from — learning those would teach a model a "limit" it
doesn't have). The idle floor is handled separately in `context_watch`.

Cost discipline, because this runs at SessionStart over a history that reaches
hundreds of MB:
  * It runs from an async hook, so it never delays a session opening.
  * Each file is read from a cached byte offset, so no byte is read twice:
    after the first scan, a startup reads only what was appended since.
  * Only lines containing the bytes `compact_boundary` are JSON-parsed.
  * A wall-clock budget bounds any one run; files it didn't reach keep their
    old offset and are picked up next session. Newest files go first.

The cache holds token counts, model ids and file offsets — never transcript
text. Every failure path leaves the cache as it was (or rebuilds it from
nothing when corrupt) and the watch falls back to its defaults.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

CACHE_VERSION = 1
DEFAULT_BUDGET_S = 2.0
DEFAULT_MAX_AGE_DAYS = 60
# A gap this long between the last reply and a compaction means the session sat
# idle — an idle compaction, not a limit one. The shortest idle gap observed
# was 67 minutes and the longest limit-compaction gap 2.4 minutes, so 30 sits
# well clear of both.
IDLE_GAP_S = 30 * 60
# Recent hard-limit samples kept per model. The point used is their minimum,
# so a one-off low value (a session run with a small CLAUDE_CODE_AUTO_COMPACT_
# WINDOW) ages out after this many later compactions instead of sticking.
MAX_SAMPLES = 5

_BOUNDARY = b"compact_boundary"
_ASSISTANT = b'"type":"assistant"'


def config_dir() -> Path:
    """Claude Code's config dir — the same root `find_transcript` uses."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def cache_path(config: "Path | None" = None) -> Path:
    return (config or config_dir()) / "clawness" / "compaction.json"


def _empty() -> dict:
    return {"v": CACHE_VERSION, "files": {}, "models": {}}


def load_cache(path: "Path | None" = None) -> dict:
    """The cache, or a fresh empty one when missing, corrupt or another version."""
    try:
        data = json.loads((path or cache_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if (not isinstance(data, dict) or data.get("v") != CACHE_VERSION
            or not isinstance(data.get("files"), dict)
            or not isinstance(data.get("models"), dict)):
        return _empty()
    return data


def learned_hard_point(model: "str | None", path: "Path | None" = None) -> "int | None":
    """Lowest recent hard-limit compaction size seen for *model*, or None."""
    if not model:
        return None
    entry = load_cache(path)["models"].get(model)
    if not isinstance(entry, dict):
        return None
    samples = [s for s in entry.get("samples", []) if isinstance(s, int) and s > 0]
    return min(samples) if samples else None


def _parse_ts(value) -> "float | None":
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _line_at(buf: bytes, pos: int) -> "tuple[int, bytes]":
    """(start, line) of the line containing byte *pos*."""
    start = buf.rfind(b"\n", 0, pos) + 1
    end = buf.find(b"\n", pos)
    return start, buf[start:end if end >= 0 else len(buf)]


def last_main_assistant(buf: bytes, end: int) -> "tuple[str, float] | None":
    """(model, timestamp) of the last main-chain assistant entry before *end*.

    The byte pattern can't match inside a content string (quotes there are
    escaped), but every hit is still parsed and checked before it's believed.
    """
    while end > 0:
        pos = buf.rfind(_ASSISTANT, 0, end)
        if pos < 0:
            return None
        start, line = _line_at(buf, pos)
        end = start
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (not isinstance(entry, dict) or entry.get("type") != "assistant"
                or entry.get("isSidechain")):
            continue
        message = entry.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        ts = _parse_ts(entry.get("timestamp"))
        if isinstance(model, str) and model and ts is not None:
            return model, ts
    return None


def _boundaries(buf: bytes):
    """Yield (line_start, entry) for each real compact_boundary record in *buf*."""
    pos = buf.find(_BOUNDARY)
    while pos >= 0:
        start, line = _line_at(buf, pos)
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if (isinstance(entry, dict) and entry.get("type") == "system"
                and entry.get("subtype") == "compact_boundary"):
            yield start, entry
        pos = buf.find(_BOUNDARY, start + len(line) + 1)


def _record(models: dict, model: str, tokens: int) -> None:
    entry = models.get(model)
    if not isinstance(entry, dict):
        entry = {"samples": []}
    samples = [s for s in entry.get("samples", []) if isinstance(s, int)]
    samples.append(int(tokens))
    entry["samples"] = samples[-MAX_SAMPLES:]
    models[model] = entry


def scan_file(path: Path, state: dict, models: dict) -> dict:
    """Read *path* from ``state["offset"]``, record hard-limit compactions into
    *models*, and return the file's new state. Pure apart from the read."""
    offset = int(state.get("offset", 0) or 0)
    size = path.stat().st_size
    if size < offset:          # rewritten or truncated — start over
        offset, state = 0, {}
    with open(path, "rb") as fh:
        fh.seek(offset)
        buf = fh.read()
    # Stop at the last complete line: a half-written one is re-read next time.
    cut = buf.rfind(b"\n") + 1
    buf = buf[:cut]

    prev_model = state.get("model") if isinstance(state.get("model"), str) else None
    prev_ts = state.get("asst_ts") if isinstance(state.get("asst_ts"), (int, float)) else None

    for start, entry in _boundaries(buf):
        meta = entry.get("compactMetadata")
        if not isinstance(meta, dict) or meta.get("trigger") != "auto":
            continue
        try:
            tokens = int(meta.get("preTokens") or 0)
        except (TypeError, ValueError):
            continue
        found = last_main_assistant(buf, start)
        model, asst_ts = found if found else (prev_model, prev_ts)
        at = _parse_ts(entry.get("timestamp"))
        if not model or tokens <= 0 or asst_ts is None or at is None:
            continue
        if at - asst_ts >= IDLE_GAP_S:
            continue
        _record(models, model, tokens)

    tail = last_main_assistant(buf, len(buf))
    if tail:
        prev_model, prev_ts = tail
    new_state = {"offset": offset + cut}
    if prev_model:
        new_state["model"] = prev_model
    if prev_ts is not None:
        new_state["asst_ts"] = prev_ts
    return new_state


def scan_history(
    config: "Path | None" = None,
    budget_s: float = DEFAULT_BUDGET_S,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    now: "float | None" = None,
) -> dict:
    """Bring the cache up to date with the transcripts under *config*; return it.

    Newest files first, bounded by *budget_s* of wall-clock time (the first file
    is always read, so a single huge transcript can't stall progress forever).
    """
    config = config or config_dir()
    path = cache_path(config)
    cache = load_cache(path)
    files, models = cache["files"], cache["models"]
    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86400

    candidates = []
    for f in (config / "projects").glob("*/*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            candidates.append((st.st_mtime, st.st_size, f))
    candidates.sort(reverse=True)

    started = time.perf_counter()
    changed = False
    for _mtime, size, f in candidates:
        key = str(f)
        state = files.get(key) if isinstance(files.get(key), dict) else {}
        if state.get("offset") == size:
            continue
        if changed and time.perf_counter() - started > budget_s:
            break
        try:
            files[key] = scan_file(f, state, models)
            changed = True
        except OSError:
            continue

    live = {str(f) for _m, _s, f in candidates}
    for key in [k for k in files if k not in live]:
        del files[key]
        changed = True

    if changed or not path.exists():
        from clawness.plan import atomic_write_text
        atomic_write_text(path, json.dumps(cache))
    return cache
