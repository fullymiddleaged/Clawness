"""New-coverage-on-upgrade note (clawness/upgrade.py)."""

import json
from pathlib import Path

import pytest

from clawness.upgrade import (
    LEDGER_NAME,
    available_domains,
    check_upgrade,
    newly_covered,
    render_note,
)

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "rules"


def _ledger(root):
    return json.loads((root / ".clawness" / LEDGER_NAME).read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".clawness").mkdir()
    return tmp_path


def test_available_domains_lists_corpus_folders_but_not_mandatory():
    doms = available_domains(RULES)
    assert "python" in doms and "security" in doms
    # _mandatory bypasses retrieval and the stack filter, so it can never be
    # "newly matched" and must not be reportable.
    assert "_mandatory" not in doms


def test_available_domains_on_a_missing_dir_is_empty_not_an_error():
    assert available_domains(Path("/no/such/rules")) == set()


def test_first_session_records_but_says_nothing(root):
    """Everything is 'new' with no ledger, which is noise, not news."""
    fresh = check_upgrade(root, "1.18.0", {"python", "general"}, RULES)
    assert fresh == []
    assert _ledger(root)["version"] == "1.18.0"
    assert _ledger(root)["covered"] == ["python"]


def test_unchanged_version_is_the_silent_fast_path(root):
    check_upgrade(root, "1.18.0", {"python", "general"}, RULES)
    before = (root / ".clawness" / LEDGER_NAME).read_text(encoding="utf-8")
    assert check_upgrade(root, "1.18.0", {"python", "typescript"}, RULES) == []
    # Same version must not even rewrite the ledger.
    assert (root / ".clawness" / LEDGER_NAME).read_text(encoding="utf-8") == before


def test_upgrade_reports_a_domain_that_now_matches(root):
    """The Astro case: the project was always Astro, the corpus caught up."""
    check_upgrade(root, "1.17.0", {"typescript", "general"}, RULES)
    fresh = check_upgrade(root, "1.18.0", {"typescript", "css", "general"}, RULES)
    assert fresh == ["css"]
    assert _ledger(root)["version"] == "1.18.0"


def test_upgrade_with_no_new_domains_is_silent(root):
    check_upgrade(root, "1.17.0", {"python", "general"}, RULES)
    assert check_upgrade(root, "1.18.0", {"python", "general"}, RULES) == []


def test_a_domain_is_reported_once_not_on_every_later_upgrade(root):
    check_upgrade(root, "1.17.0", {"typescript"}, RULES)
    assert check_upgrade(root, "1.18.0", {"typescript", "css"}, RULES) == ["css"]
    assert check_upgrade(root, "1.19.0", {"typescript", "css"}, RULES) == []


def test_always_on_domains_are_never_reported(root):
    check_upgrade(root, "1.17.0", {"python"}, RULES)
    assert check_upgrade(root, "1.18.0", {"python", "general", "workflows"}, RULES) == []


def test_a_domain_with_no_rules_is_not_reported(root):
    """Detection can name a domain the corpus has no folder for; it isn't coverage."""
    check_upgrade(root, "1.17.0", {"python"}, RULES)
    assert check_upgrade(root, "1.18.0", {"python", "haskell"}, RULES) == []


def test_a_corrupt_ledger_stays_silent_and_repairs_itself(root):
    """Opposite of coverage.unasked, which warns twice rather than never: a
    spurious 'new coverage' note is the false alarm that teaches users to ignore
    the real one, so an unreadable ledger is treated as a first session."""
    (root / ".clawness" / LEDGER_NAME).write_text("{not json", encoding="utf-8")
    assert check_upgrade(root, "1.18.0", {"python", "css"}, RULES) == []
    assert _ledger(root)["version"] == "1.18.0"


def test_newly_covered_is_sorted_and_deduped():
    assert newly_covered({"css", "python", "go"}, RULES, ["python"]) == ["css", "go"]


def test_note_is_empty_without_labels():
    assert render_note([], "1.18.0") == ""


def test_note_names_the_version_and_labels_and_commissions_nothing():
    note = render_note(["astro", "css"], "1.18.0")
    assert "1.18.0" in note and "astro, css" in note
    assert "CLAW_NO_UPGRADE_NOTE" in note
    # The rules are already live. A note that reads as a work order becomes the
    # session's task — the failure documented twice in CLAUDE.md.
    assert "already active" in note
    assert "don't start any work" in note.lower()
