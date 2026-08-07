"""Version-range grammar, the trust invariant, the note text, and the ledger."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from clawness.core import Rule
from clawness.init import VERSION_WATCH_JS, VERSION_WATCH_PY
from clawness.staleness import (
    SESSION_BACKSTOP,
    WATCHED_LABELS,
    is_above_ceiling,
    is_armed,
    parse_range,
    render_note,
    stale_rules,
    summarize,
    unasked,
)

REPO = Path(__file__).resolve().parent.parent
STACK_DETECT = REPO / "hooks" / "stack_detect.py"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _rule(rule_id="NX-ROUTE-001", **kw):
    return Rule(id=rule_id, domain=kw.pop("domain", "nextjs"), **kw)


# ── Grammar ───────────────────────────────────────────────────────

class TestParseRange:
    @pytest.mark.parametrize("spec", ["13-15", "15", "1.4-2.0", " 13 - 15 ", "2"])
    def test_valid_specs_parse(self, spec):
        assert parse_range(spec) is not None

    @pytest.mark.parametrize("spec", [
        "13-",          # open-ended: the claim nobody has evidence for
        "-15",
        "*",
        "latest",
        "",
        "^13",
        ">=13 <=15",    # npm comparator syntax is not the grammar
        "1.2.3",        # three components claim precision the detector lacks
        "16-13",        # inverted
        "13--15",
    ])
    def test_unparseable_specs_yield_none(self, spec):
        assert parse_range(spec) is None

    def test_a_single_version_is_a_range_of_one(self):
        assert parse_range("15") == parse_range("15-15")


class TestIsAboveCeiling:
    @pytest.mark.parametrize("spec,detected,expected", [
        ("13-15", "17", True),      # the case the feature exists for
        ("13-15", "16", True),
        ("13-15", "15", False),
        ("13-15", "12", False),     # below the floor is deliberately silent
        ("1.4-2.0", "2.1", True),
        ("1.4-2.0", "2.0", False),
        ("1.4-2.0", "1.4", False),
        ("15", "15.9", False),      # a bare ceiling covers that whole major
        ("15", "16", True),
    ])
    def test_comparison(self, spec, detected, expected):
        assert is_above_ceiling(spec, detected) is expected

    @pytest.mark.parametrize("detected", ["", "*", "latest", "workspace:^", "next"])
    def test_an_unusable_detected_version_stays_silent(self, detected):
        """`_clean_version` yields "" for a git URL or `latest`; a wrong version
        is worse than none, so the warning must not fire on one."""
        assert is_above_ceiling("13-15", detected) is False

    def test_an_unusable_spec_stays_silent(self):
        assert is_above_ceiling(">=13", "17") is False


# ── The trust invariant ───────────────────────────────────────────

class TestIsArmed:
    def test_a_full_stamp_arms(self):
        assert is_armed(_rule(
            applies_to={"Next.js": "13-15"},
            verified="2026-08",
            sources=["https://nextjs.org/docs"],
        )) is True

    @pytest.mark.parametrize("kw", [
        {"applies_to": {"Next.js": "13-15"}},                        # asserted, not verified
        {"applies_to": {"Next.js": "13-15"}, "verified": "2026-08"},  # no evidence
        {"applies_to": {"Next.js": "13-15"}, "sources": ["https://x"]},  # no review date
        {"verified": "2026-08", "sources": ["https://x"]},            # no range
        {},
    ])
    def test_an_incomplete_stamp_stays_silent(self, kw):
        assert is_armed(_rule(**kw)) is False


class TestStaleRules:
    def test_an_exceeded_stamp_is_reported(self):
        rule = _rule(
            applies_to={"Next.js": "13-15"},
            verified="2026-08",
            sources=["https://nextjs.org/docs"],
        )
        assert stale_rules([rule], {"Next.js": "17"}) == [(rule, "Next.js", "17")]

    def test_an_unverified_rule_never_warns(self):
        """The invariant, at the level a caller sees it: a project version that
        would otherwise trigger produces nothing without evidence."""
        rule = _rule(applies_to={"Next.js": "13-15"})
        assert stale_rules([rule], {"Next.js": "17"}) == []

    def test_an_unstamped_rule_never_warns(self):
        assert stale_rules([_rule()], {"Next.js": "17"}) == []

    def test_an_undetected_framework_never_warns(self):
        rule = _rule(
            applies_to={"Django": "4-5"},
            verified="2026-08",
            sources=["https://docs.djangoproject.com"],
        )
        assert stale_rules([rule], {"Next.js": "17"}) == []

    def test_no_detected_versions_at_all(self):
        rule = _rule(
            applies_to={"Next.js": "13-15"},
            verified="2026-08",
            sources=["https://nextjs.org/docs"],
        )
        assert stale_rules([rule], {}) == []

    def test_rules_are_judged_individually_not_by_domain(self):
        """The regression per-rule stamping exists to prevent: a narrow rule
        warns while a wider one in the same domain stays silent, on one project.
        A domain-wide stamp is the union of its rules — the widest claim, and
        wide is the direction that fails silent."""
        narrow = _rule("NX-CACHE-001",
                       applies_to={"Next.js": "13-15"},
                       verified="2026-08", sources=["https://nextjs.org/docs"])
        wide = _rule("NX-ROUTE-001",
                     applies_to={"Next.js": "13-17"},
                     verified="2026-08", sources=["https://nextjs.org/docs"])

        reported = stale_rules([narrow, wide], {"Next.js": "17"})

        assert [r.id for r, _, _ in reported] == ["NX-CACHE-001"]

    def test_a_rule_is_reported_once_even_naming_several_frameworks(self):
        rule = _rule(
            applies_to={"Next.js": "13-15", "React": "17-18"},
            verified="2026-08", sources=["https://nextjs.org/docs"],
        )
        assert len(stale_rules([rule], {"Next.js": "17", "React": "19"})) == 1


# ── The join key ──────────────────────────────────────────────────

class TestWatchedLabels:
    def test_labels_are_the_detector_labels_not_package_names(self):
        assert "Next.js" in WATCHED_LABELS
        assert "next" not in WATCHED_LABELS
        assert "SQLAlchemy" in WATCHED_LABELS
        assert "sqlalchemy" not in WATCHED_LABELS

    def test_every_watched_package_contributes_its_label(self):
        expected = {label for _pkg, label in (*VERSION_WATCH_JS, *VERSION_WATCH_PY)}
        assert WATCHED_LABELS == expected


# ── The note text ─────────────────────────────────────────────────
#
# This is the one test that guards the regression that actually happened: an
# earlier version of this feature had Clawness write heaps of new rules and
# degrade a session opened for something else, and 1.7.0's CLAUDE.md remedy did
# the same from a SessionStart note. The bug was the timing, not the content — a
# note fires before the user has said what they came for — so the note's text is
# a reviewed artifact and asserted on directly.

def _armed(rule_id, label="Next.js", spec="13-15"):
    return Rule(id=rule_id, domain="nextjs", applies_to={label: spec},
                verified="2026-08", sources=["https://nextjs.org/docs"])


@pytest.fixture
def note():
    stale = stale_rules([_armed("NX-ROUTE-001"), _armed("NX-CACHE-001")],
                        {"Next.js": "17"})
    return render_note(summarize(stale))


class TestNoteText:
    def test_it_states_the_gap_with_real_numbers(self, note):
        assert "2 Next.js rules" in note
        assert "up to 15" in note
        assert "declares 17" in note

    def test_it_commissions_no_work(self, note):
        """A note that reads as a work order becomes the session's task."""
        lowered = note.lower()
        for imperative in ("research this", "audit the rules", "look up",
                           "review the corpus", "update the rules"):
            assert imperative not in lowered
        assert "do not go looking now" in lowered
        assert "no research pass" in lowered
        assert "no writing rule files" in lowered

    def test_it_names_memory_md_and_never_the_rules_dir(self, note):
        """`.clawness/rules/` is the path that produced the heaps."""
        assert ".clawness/memory.md" in note
        assert ".clawness/rules" not in note

    def test_it_states_the_session_backstop(self, note):
        assert f"at most {SESSION_BACKSTOP} this session" in note

    def test_it_names_the_remedy_without_starting_it(self, note):
        """1.8.0's shape: the note names the command, the user's typing it is
        the consent, and the note starts nothing."""
        assert "/clawness:refresh" in note
        assert "when it suits them" in note

    def test_it_carries_its_own_opt_out(self, note):
        assert "CLAW_NO_STALENESS_NOTE=1" in note

    def test_nothing_stale_means_no_note(self):
        assert render_note([]) == ""

    def test_one_stale_rule_reads_as_singular(self):
        stale = stale_rules([_armed("NX-ROUTE-001")], {"Next.js": "17"})
        assert "1 Next.js rule verified" in render_note(summarize(stale))

    def test_several_frameworks_are_each_reported(self):
        rules = [
            _armed("NX-ROUTE-001"),
            _armed("SA-001", label="SQLAlchemy", spec="1.4-2.0"),
        ]
        stale = stale_rules(rules, {"Next.js": "17", "SQLAlchemy": "2.1"})
        note = render_note(summarize(stale))
        assert "Next.js" in note and "SQLAlchemy" in note

    def test_the_highest_verified_bound_is_the_one_reported(self):
        """Rules in one domain carry different ranges; the note must report the
        furthest any of them was actually verified to, not the first it saw."""
        rules = [_armed("A", spec="13-14"), _armed("B", spec="13-15")]
        stale = stale_rules(rules, {"Next.js": "17"})
        assert "up to 15" in render_note(summarize(stale))


# ── The ledger ────────────────────────────────────────────────────

class TestLedger:
    def test_a_first_warning_passes_and_a_repeat_does_not(self, tmp_path):
        summaries = [("Next.js", "15", "17", 2)]
        assert unasked(tmp_path, summaries) == summaries
        assert unasked(tmp_path, summaries) == []

    def test_a_new_major_re_arms(self, tmp_path):
        unasked(tmp_path, [("Next.js", "15", "17", 2)])
        assert unasked(tmp_path, [("Next.js", "15", "18", 2)]) != []

    def test_it_is_not_time_sensitive(self, tmp_path):
        """Keyed on the fact, not a date: a recorded ask stays suppressed however
        long ago it was, and a version move fires immediately however recent."""
        unasked(tmp_path, [("Next.js", "15", "17", 2)])
        ledger = json.loads(
            (tmp_path / ".clawness" / "staleness.json").read_text(encoding="utf-8")
        )
        ledger["updated"] = 0  # as if asked in 1970
        (tmp_path / ".clawness" / "staleness.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        assert unasked(tmp_path, [("Next.js", "15", "17", 2)]) == []

    def test_frameworks_are_tracked_independently(self, tmp_path):
        unasked(tmp_path, [("Next.js", "15", "17", 2)])
        fresh = unasked(tmp_path, [("Next.js", "15", "17", 2),
                                   ("SQLAlchemy", "2.0", "2.1", 1)])
        assert [s[0] for s in fresh] == ["SQLAlchemy"]

    def test_an_unreadable_ledger_warns_rather_than_going_silent(self, tmp_path):
        (tmp_path / ".clawness").mkdir()
        (tmp_path / ".clawness" / "staleness.json").write_text("{not json",
                                                              encoding="utf-8")
        assert unasked(tmp_path, [("Next.js", "15", "17", 2)]) != []

    def test_nothing_stale_writes_no_ledger(self, tmp_path):
        assert unasked(tmp_path, []) == []
        assert not (tmp_path / ".clawness" / "staleness.json").exists()


# ── End to end, through the real hook ─────────────────────────────

STAMPED_RULE = """\
id: NX-ROUTE-001
domain: nextjs
severity: warning
tags: [routing]
triggers: [route]
when: Defining routes in a Next.js app.
rule: Use the App Router directory conventions for new routes.
applies_to: {"Next.js": "13-15"}
verified: "2026-08"
sources: ["https://nextjs.org/docs"]
"""

UNSTAMPED_RULE = STAMPED_RULE.split("applies_to:")[0]


def _fixture(rule_yaml: str, next_version: str) -> tuple[Path, Path]:
    """A git project declaring *next_version*, plus a rules dir holding one rule."""
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True)
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"next": next_version}}), encoding="utf-8"
    )
    rules = Path(tempfile.mkdtemp()) / "rules"
    (rules / "nextjs").mkdir(parents=True)
    (rules / "nextjs" / "NX-ROUTE-001.yml").write_text(rule_yaml, encoding="utf-8")
    return root, rules


def _run_hook(root: Path, rules: Path, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("CLAW_NO_STACK_NOTE", None)
    env.pop("CLAW_NO_STALENESS_NOTE", None)
    env["CLAW_RULES_DIR"] = str(rules)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(STACK_DETECT)],
        input=json.dumps({"cwd": str(root)}),
        capture_output=True, text=True, env=env,
    ).stdout


@needs_git
class TestThroughTheHook:
    def test_a_project_past_the_stamp_is_warned_once(self):
        root, rules = _fixture(STAMPED_RULE, "^17.0.0")

        first = _run_hook(root, rules)
        assert "Version gap" in first
        assert "1 Next.js rule verified only up to 15" in first

        # Once per mismatch, not once per session.
        assert "Version gap" not in _run_hook(root, rules)

    def test_a_project_inside_the_stamp_stays_silent(self):
        root, rules = _fixture(STAMPED_RULE, "^15.1.0")
        assert "Version gap" not in _run_hook(root, rules)

    def test_an_unstamped_rule_stays_silent(self):
        root, rules = _fixture(UNSTAMPED_RULE, "^17.0.0")
        assert "Version gap" not in _run_hook(root, rules)

    def test_an_unparseable_declared_version_stays_silent(self):
        """A git URL or `latest` yields "" from `_clean_version`; a wrong version
        is worse than none."""
        root, rules = _fixture(STAMPED_RULE, "latest")
        assert "Version gap" not in _run_hook(root, rules)

    def test_the_opt_out_silences_it_without_touching_the_stack_note(self):
        root, rules = _fixture(STAMPED_RULE, "^17.0.0")
        out = _run_hook(root, rules, {"CLAW_NO_STALENESS_NOTE": "1"})
        assert "Version gap" not in out
        assert "Detected project stack" in out

    def test_a_project_rule_stamp_overrides_the_global_one(self):
        """Rules written by `/clawness:refresh` are stamped at the major they
        were checked against — so they must be subject to the same check, and
        must win over the global rule they shadow."""
        root, rules = _fixture(STAMPED_RULE, "^17.0.0")
        project_rules = root / ".clawness" / "rules" / "nextjs"
        project_rules.mkdir(parents=True)
        (project_rules / "NX-ROUTE-001.yml").write_text(
            STAMPED_RULE.replace('"13-15"', '"13-17"'), encoding="utf-8"
        )
        assert "Version gap" not in _run_hook(root, rules)

    def test_a_generated_rule_goes_stale_when_the_project_moves_again(self):
        root, rules = _fixture(STAMPED_RULE, "^18.0.0")
        project_rules = root / ".clawness" / "rules" / "nextjs"
        project_rules.mkdir(parents=True)
        (project_rules / "NX-ROUTE-001.yml").write_text(
            STAMPED_RULE.replace('"13-15"', '"13-17"'), encoding="utf-8"
        )
        out = _run_hook(root, rules)
        assert "1 Next.js rule verified only up to 17" in out
