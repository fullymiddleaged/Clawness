"""
Tests for clawness/compaction.py — learning where sessions compact from history.

Runs under pytest, or standalone:  python tests/test_compaction.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness import compaction as C  # noqa: E402

T0 = "2026-11-01T10:00:00.000Z"


def _ts(minutes: float) -> str:
    base = time.mktime(time.strptime("2026-11-01 10:00:00", "%Y-%m-%d %H:%M:%S"))
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base + minutes * 60))


def _assistant(model: str, minute: float, sidechain: bool = False) -> dict:
    return {"type": "assistant", "isSidechain": sidechain, "timestamp": _ts(minute),
            "message": {"model": model, "role": "assistant",
                        "content": [{"type": "text", "text": "SECRET-TRANSCRIPT-TEXT"}],
                        "usage": {"input_tokens": 1}}}


def _user(minute: float, text: str = "hi") -> dict:
    return {"type": "user", "timestamp": _ts(minute),
            "message": {"role": "user", "content": text}}


def _boundary(minute: float, pre: int, trigger: str = "auto") -> dict:
    return {"type": "system", "subtype": "compact_boundary", "timestamp": _ts(minute),
            "compactMetadata": {"trigger": trigger, "preTokens": pre}}


def _config(*sessions: "list[dict]") -> Path:
    cfg = Path(tempfile.mkdtemp())
    proj = cfg / "projects" / "c--proj"
    proj.mkdir(parents=True)
    for i, entries in enumerate(sessions):
        _write(proj / f"s{i}.jsonl", entries)
    return cfg


def _write(path: Path, entries, mode: str = "w") -> None:
    with open(path, mode, encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")


def _learned(cfg: Path, model: str):
    return C.learned_hard_point(model, C.cache_path(cfg))


def test_a_limit_compaction_is_learned():
    cfg = _config([_assistant("claude-opus-4-6", 0), _user(1), _boundary(2, 167_599)])
    C.scan_history(cfg)
    assert _learned(cfg, "claude-opus-4-6") == 167_599


def test_an_idle_compaction_is_not_learned():
    # The 1M-model case: 90 minutes idle, then compacted at whatever size.
    cfg = _config([_assistant("claude-opus-5-5", 0), _user(90), _boundary(90.1, 201_417)])
    C.scan_history(cfg)
    assert _learned(cfg, "claude-opus-5-5") is None


def test_a_manual_compact_is_not_learned():
    cfg = _config([_assistant("claude-opus-4-6", 0), _boundary(1, 90_000, "manual")])
    C.scan_history(cfg)
    assert _learned(cfg, "claude-opus-4-6") is None


def test_the_lowest_recent_sample_wins_and_old_ones_age_out():
    entries = []
    for i, pre in enumerate([150_000] + [168_000] * C.MAX_SAMPLES):
        entries += [_assistant("m", i * 10), _boundary(i * 10 + 1, pre)]
    cfg = _config(entries)
    C.scan_history(cfg)
    assert _learned(cfg, "m") == 168_000   # the 150k sample was pushed out


def test_a_sidechain_reply_is_not_mistaken_for_the_session_model():
    cfg = _config([_assistant("claude-opus-4-6", 0), _user(1),
                   _assistant("claude-haiku-4-5", 1.5, sidechain=True),
                   _boundary(2, 167_000)])
    C.scan_history(cfg)
    assert _learned(cfg, "claude-opus-4-6") == 167_000
    assert _learned(cfg, "claude-haiku-4-5") is None


def test_quoted_boundary_text_in_a_message_is_ignored():
    """This module's own source gets discussed in transcripts: the bytes
    'compact_boundary' inside a message must not count as a compaction."""
    cfg = _config([_assistant("m", 0),
                   _user(1, 'look for "subtype":"compact_boundary","preTokens":5'),
                   ])
    C.scan_history(cfg)
    assert C.load_cache(C.cache_path(cfg))["models"] == {}


def test_a_rescan_reads_only_appended_bytes_and_carries_the_model_over():
    cfg = _config([_assistant("claude-opus-4-6", 0), _user(1)])
    path = cfg / "projects" / "c--proj" / "s0.jsonl"
    C.scan_history(cfg)
    first = C.load_cache(C.cache_path(cfg))["files"][str(path)]
    assert first["offset"] == path.stat().st_size and first["model"] == "claude-opus-4-6"

    # The assistant entry now lies BEFORE the read offset: the cached model and
    # timestamp must stand in for it.
    _write(path, [_boundary(2, 167_000)], mode="a")
    C.scan_history(cfg)
    assert _learned(cfg, "claude-opus-4-6") == 167_000
    assert C.load_cache(C.cache_path(cfg))["files"][str(path)]["offset"] == path.stat().st_size


def test_a_half_written_last_line_is_re_read_next_time():
    cfg = _config([_assistant("m", 0)])
    path = cfg / "projects" / "c--proj" / "s0.jsonl"
    line = json.dumps(_boundary(1, 160_000), separators=(",", ":"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line[:40])                     # mid-write
    C.scan_history(cfg)
    assert _learned(cfg, "m") is None
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line[40:] + "\n")              # finished
    C.scan_history(cfg)
    assert _learned(cfg, "m") == 160_000


def test_a_truncated_file_is_rescanned_from_the_start():
    cfg = _config([_assistant("m", 0), _user(1), _user(1), _user(1)])
    path = cfg / "projects" / "c--proj" / "s0.jsonl"
    C.scan_history(cfg)
    _write(path, [_assistant("m", 0), _boundary(1, 150_000)])   # rewritten, shorter
    C.scan_history(cfg)
    assert _learned(cfg, "m") == 150_000


def test_the_budget_stops_early_and_the_next_run_resumes():
    cfg = _config(*[[_assistant(f"m{i}", 0), _boundary(1, 160_000 + i)] for i in range(4)])
    cache = C.scan_history(cfg, budget_s=0.0)
    assert len(cache["files"]) == 1             # first file always read
    for _ in range(3):
        cache = C.scan_history(cfg, budget_s=0.0)
    assert len(cache["files"]) == 4
    assert all(_learned(cfg, f"m{i}") == 160_000 + i for i in range(4))


def test_files_older_than_the_window_are_skipped():
    cfg = _config([_assistant("m", 0), _boundary(1, 160_000)])
    path = cfg / "projects" / "c--proj" / "s0.jsonl"
    old = time.time() - 90 * 86400
    os.utime(path, (old, old))
    C.scan_history(cfg)
    assert _learned(cfg, "m") is None


def test_a_corrupt_cache_is_rebuilt():
    cfg = _config([_assistant("m", 0), _boundary(1, 160_000)])
    p = C.cache_path(cfg)
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    C.scan_history(cfg)
    assert _learned(cfg, "m") == 160_000


def test_the_cache_never_holds_transcript_text():
    cfg = _config([_assistant("m", 0), _user(1, "my password is hunter2"),
                   _boundary(2, 160_000)])
    C.scan_history(cfg)
    text = C.cache_path(cfg).read_text(encoding="utf-8")
    assert "hunter2" not in text and "SECRET-TRANSCRIPT-TEXT" not in text


def test_deleted_transcripts_drop_out_of_the_cache():
    cfg = _config([_assistant("m", 0)])
    path = cfg / "projects" / "c--proj" / "s0.jsonl"
    C.scan_history(cfg)
    path.unlink()
    assert C.scan_history(cfg)["files"] == {}


def test_no_history_at_all_is_quiet():
    cfg = Path(tempfile.mkdtemp())
    assert C.scan_history(cfg)["models"] == {}
    assert C.learned_hard_point("anything", C.cache_path(cfg)) is None
    assert C.learned_hard_point(None) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all compaction tests passed")
