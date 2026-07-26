"""
Model-tier advisor: notice when the session's model looks mismatched to the work.

A user picks a model tier once and rarely revisits it. Nothing in the session ever
says "this task is deeper than the tier you're on" — or the reverse. This module
looks at the opening prompt of a session and the tier it's running, and hands the
hook a short note when the mismatch is strong enough to be worth a sentence.

Four things shape the design, and undoing any of them brings back a real failure:

* **Evidence, not verdict.** The note reports the signals found and lets Claude —
  which can read the actual task and knows its own identity — decide whether to
  raise it at all. The heuristic here WILL be wrong sometimes; letting Claude
  filter means a wrong guess usually dies before the user ever sees it. A hook
  that asserted "switch to X" would surface every false positive.
* **Asymmetric thresholds.** An upgrade hint fires on a moderate signal. A
  downgrade hint needs a strong one, an absence of *any* upgrade signal, and a
  short prompt. Getting an upgrade wrong costs money the user can see; getting a
  downgrade wrong means they silently receive a shallower answer on hard work and
  never learn that's what happened. Only one of those is self-correcting.
* **Never rank the top tier internally.** opus and fable are both TIER_TOP. There
  is no defensible ordering between them, so a session on either is never told to
  move.
* **Fails silent, not open.** Unlike the context watch (which fails toward
  alerting, because a missed warning is worse than a repeat), an unknown model or
  an unreadable ledger here means say nothing. Silence costs nothing; an
  unprompted, wrong opinion about someone's spend costs trust.

Pure logic plus one small JSON ledger. No model, no heavy imports — this is
imported by the per-prompt hook.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

TIER_LOW = 1     # haiku-class
TIER_MID = 2     # sonnet-class
TIER_TOP = 3     # opus / fable — never ordered against each other

_TIER_LABELS = {TIER_LOW: "a small/fast model", TIER_MID: "a mid-tier model",
                TIER_TOP: "a top-tier model"}

# Matched against the lowercased model string, so every form Claude Code reports
# is covered: the alias ("opus"), a full id ("claude-opus-5"), and a window
# suffix ("opus[1m]"). Order matters only in that a string somehow naming two
# tiers resolves to the first match.
_TIER_PATTERNS = (
    (TIER_LOW, re.compile(r"haiku")),
    (TIER_TOP, re.compile(r"opus|fable")),
    (TIER_MID, re.compile(r"sonnet")),
)

# How many distinct signal groups must fire. Deliberately different per direction
# — see the asymmetry note in the module docstring.
UPGRADE_MIN_SIGNALS = 2
DOWNGRADE_MIN_SIGNALS = 2
# A downgrade hint additionally requires a SHORT prompt: a long brief describing
# routine-sounding work is usually not routine.
DOWNGRADE_MAX_WORDS = 25
# Below this, there is no prompt to judge ("continue", "yes", "go on").
MIN_WORDS = 3

# Grouped so that two mentions of the same idea count once — "optimize the slow
# query" is one signal (performance), not two. Names are shown to the user, so
# they read as reasons rather than regex.
_UPGRADE_SIGNALS: tuple[tuple[str, str], ...] = (
    ("architecture", r"\barchitect\w*|\bdesign\b|\bredesign\b|\btopology\b"),
    ("migration", r"\bmigrat\w+|\bupgrade\s+from\b|\bcut\s?over\b|\bbackfill\b"),
    ("concurrency", r"\bconcurren\w+|\brace condition\b|\bdeadlock\b|\bmutex\b|\bthread[- ]?safe\b"),
    ("security", r"\bsecurity\b|\bvulnerab\w+|\bexploit\w*|\bauth[nz]\b|\bcsrf\b|\bxss\b|\binjection\b|\bthreat model\b"),
    ("diagnosis", r"\bwhy (?:is|does|do|are|did)\b|\broot cause\b|\bintermittent\b|\bflaky\b|\bcan(?:'|no)?t figure out\b"),
    ("performance", r"\bperformance\b|\boptimi[sz]\w+|\bbottleneck\b|\bprofil\w+|\bn\+1\b"),
    ("trade-off", r"\btrade[- ]?offs?\b|\bversus\b|\bvs\.?\s|\bshould (?:we|i)\b|\bwhich approach\b|\bpros and cons\b"),
    ("large refactor", r"\brefactor\w*|\brewrite\b|\boverhaul\b|\brestructur\w+|\bdecoupl\w+"),
    ("distributed state", r"\bdistributed\b|\bconsistency\b|\bidempoten\w+|\btransaction\w*|\beventual\w+"),
)

_DOWNGRADE_SIGNALS: tuple[tuple[str, str], ...] = (
    ("typo", r"\btypos?\b|\bspelling\b|\bmisspell\w*"),
    ("rename", r"\brename\b"),
    ("comment/docstring", r"\bcomments?\b|\bdocstring\b"),
    # (?:re)? because \b never matches inside "reformat" — the prefix has to be
    # spelled out or the most common phrasing of this signal is missed entirely.
    ("formatting", r"\b(?:re)?format\w*|\bwhitespace\b|\bindent\w*|\blint\b|\bsort the imports\b"),
    ("version bump", r"\bbump\b|\bversion bump\b"),
    ("docs", r"\breadme\b|\bchangelog\b"),
    ("one-liner", r"\bone[- ]?liner?\b|\bsingle line\b|\bone line\b"),
)


@dataclass
class Advice:
    """A tier mismatch worth mentioning. `direction` is "up" or "down"."""

    direction: str
    tier: int
    signals: tuple[str, ...]

    @property
    def tier_label(self) -> str:
        return _TIER_LABELS.get(self.tier, "this model")


def normalize_tier(model: "str | None") -> "int | None":
    """Coarse tier for a Claude Code model string, or None when unrecognized.

    None is the common, safe answer: `inherit`, a provider-prefixed id we don't
    know, or a missing field all land here and the advisor then stays quiet.
    """
    if not model or not isinstance(model, str):
        return None
    low = model.lower()
    for tier, pattern in _TIER_PATTERNS:
        if pattern.search(low):
            return tier
    return None


def read_settings_model() -> "str | None":
    """The model recorded in Claude Code's settings, or None.

    Used as the fallback when SessionStart's payload carries no `model` field —
    the docs state it is optional. settings.local.json wins over settings.json,
    matching Claude Code's own precedence.
    """
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    for name in ("settings.local.json", "settings.json"):
        try:
            data = json.loads((config / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        model = data.get("model")
        if isinstance(model, str) and model.strip():
            return model
    return None


def assess(prompt: str, tier: "int | None") -> "Advice | None":
    """Whether this opening prompt looks mismatched to *tier*.

    Returns None for the overwhelmingly common case — that silence is the feature.
    """
    # isinstance, not just a None check: callers compose this with normalize_tier,
    # and a stray model string arriving here must fail silent rather than blow up
    # the prompt hook on a `str < int` comparison.
    if not isinstance(tier, int) or isinstance(tier, bool) or not prompt:
        return None
    words = prompt.split()
    if len(words) < MIN_WORDS:
        return None
    low = prompt.lower()

    up = tuple(name for name, pat in _UPGRADE_SIGNALS if re.search(pat, low))
    down = tuple(name for name, pat in _DOWNGRADE_SIGNALS if re.search(pat, low))

    if tier < TIER_TOP and len(up) >= UPGRADE_MIN_SIGNALS:
        return Advice("up", tier, up)

    # Downgrade is the dangerous direction, so it must clear three bars, not one:
    # enough routine signals, NO deep-work signal anywhere, and a short prompt.
    if (
        tier == TIER_TOP
        and not up
        and len(down) >= DOWNGRADE_MIN_SIGNALS
        and len(words) <= DOWNGRADE_MAX_WORDS
    ):
        return Advice("down", tier, down)

    return None


def _ledger_path(root: Path) -> Path:
    return root / ".clawness" / "model_advice.json"


def should_advise(root: Path, tier: int) -> bool:
    """True if *tier* hasn't been advised for this project yet; records it.

    Keyed on the tier, in a per-project file, so staying on a tier never re-nags
    across sessions while a tier CHANGE re-arms the check. Returns False on a
    falsy tier. A ledger that can't be written still returns True — the advice is
    worth giving once, and the note deliberately promises nothing about repeats.
    """
    if not tier:
        return False
    path = _ledger_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        advised = data.get("advised") if isinstance(data, dict) else None
        advised = advised if isinstance(advised, dict) else {}
    except (OSError, ValueError):
        advised = {}

    if str(tier) in advised:
        return False

    advised[str(tier)] = time.time()
    try:
        from clawness.plan import atomic_write_text
        atomic_write_text(path, json.dumps({"advised": advised}, indent=2) + "\n")
    except Exception:
        pass
    return True


def render_advice(advice: "Advice | None") -> str:
    """Format the advice as a note for the hook to inject, or "" for none.

    Written as an instruction to Claude, not a message to the user, because a
    UserPromptSubmit hook cannot address the user directly — the same pattern
    git_check and the context watch use. It hands over the signals and explicitly
    licenses Claude to say nothing, which is what keeps a wrong guess cheap.
    """
    if advice is None:
        return ""
    why = ", ".join(advice.signals)

    if advice.direction == "up":
        body = (
            f"This session is running {advice.tier_label}, and the opening task reads "
            f"like deep-reasoning work (signals: {why}). Judge for yourself against the "
            "actual request. If it genuinely is that involved, mention once and briefly "
            "that a higher tier may suit it better and how to switch (/model). If the "
            "task is really routine, say nothing at all — do not raise it later in the "
            "session either."
        )
    else:
        body = (
            f"This session is running {advice.tier_label}, and the opening task reads "
            f"like routine work (signals: {why}). Judge for yourself against the actual "
            "request. If it truly is this simple, you may note once that a cheaper tier "
            "would do and how to switch (/model). If there is any real depth to it — "
            "subtle logic, an unclear cause, wide blast radius — say nothing, and "
            "prefer staying where they are. Never raise it later in the session."
        )

    return (
        "--- CLAWNESS MODEL CHECK ---\n"
        f"[Clawness] {body}\n"
        "--- END CLAWNESS MODEL CHECK ---"
    )
