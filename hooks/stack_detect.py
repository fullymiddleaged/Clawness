#!/usr/bin/env python3
"""
Clawness — project stack awareness (SessionStart).

Detects the project's language/framework stack from its files and injects a
concise note so Claude starts the session already knowing "this is a Python +
FastAPI project" rather than inferring it. Standing context, complementary to
the per-prompt rule retrieval (which surfaces matching rules as you work).

Reuses the same detection as `clawness init` (one source of truth). Silent when
nothing recognizable is found, in non-project locations (home dir / filesystem
root), or when disabled via CLAW_NO_STACK_NOTE. Fails open on any error.

Carries a second, independently gated concern: **corpus staleness**
(`check_staleness`, logic in `clawness/staleness.py`). The versions this hook
already parses are the ones a rule's `applies_to` stamp is compared against, so
the check rides here rather than spawning another process — and SessionStart is
the only place it may live, since `scan_project` re-runs uncached on every
prompt. Its own opt-out is CLAW_NO_STALENESS_NOTE.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Pins UTF-8 stdio at import and puts the repo on sys.path.
from _hookutil import git_root, read_payload, session_cwd  # noqa: E402

# Stack domains → human labels, in the order we present them (languages, then
# frameworks, then infra). 'general'/'workflows' are always-on, not stack signals.
_LABELS = [
    ("python", "Python"), ("typescript", "TypeScript"), ("go", "Go"),
    ("rust", "Rust"), ("java", "Java"), ("bash", "Bash"),
    ("fastapi", "FastAPI"), ("nextjs", "Next.js"), ("react", "React"),
    ("capacitor", "Capacitor"), ("css", "CSS"),
    ("sql", "SQL"), ("docker", "Docker"),
    # Scientific computing. Listed after the web stack so a mixed repo reads
    # "Python, Fortran" rather than leading with the minority language.
    ("julia", "Julia"), ("fortran", "Fortran"), ("matlab", "MATLAB"), ("r", "R"),
    ("cfd", "CFD"),
]


def _project_root(cwd_path: Path) -> Path:
    """The git work-tree root if there is one, else cwd — so we scan the project
    top, not whatever subfolder the session happened to open in.

    Unlike the other note hooks this falls back to cwd rather than going silent:
    a stack is worth reporting for a directory that isn't a git repo yet.
    """
    return git_root(cwd_path) or cwd_path


def main() -> None:
    payload = read_payload()
    if payload is None:
        sys.exit(0)

    # Stash the active model for the model-tier advisor. ONLY SessionStart carries
    # a `model` field (and even then it's optional, hence the settings fallback);
    # UserPromptSubmit never does, so it has to be carried forward for the
    # per-prompt hook to compare tier against the task. Deliberately before the
    # stack-note opt-out below — the two features are independent.
    if not os.environ.get("CLAW_NO_MODEL_ADVISOR"):
        try:
            from clawness.model_advisor import read_settings_model
            from clawness.session_state import record_model
            record_model(
                payload.get("session_id", "") or "",
                payload.get("model") or read_settings_model() or "",
            )
        except Exception:
            pass

    if os.environ.get("CLAW_NO_STACK_NOTE"):
        sys.exit(0)

    # Don't scan non-project locations (home directory or filesystem root).
    cwd_path = session_cwd(payload)
    if cwd_path is None:
        sys.exit(0)

    try:
        from clawness.init import scan_project
        scan = scan_project(_project_root(cwd_path))
        domains = set(scan.get("domains", []))
        versions = scan.get("versions", {}) or {}
    except Exception:
        sys.exit(0)

    # A detected framework carries its declared major where we have one: "Next.js 14"
    # says far more than "Next.js", and it is the difference between App Router advice
    # and Pages Router advice. Versions we couldn't read are simply omitted — a guessed
    # version would be worse than none, since it would be acted on.
    labels = [
        f"{label} {versions[label]}" if label in versions else label
        for key, label in _LABELS if key in domains
    ]
    # Frameworks worth versioning that aren't stack labels in their own right
    # (Pydantic, SQLAlchemy, Tailwind, pandas...) still matter to the code written.
    extra = [f"{label} {v}" for label, v in sorted(versions.items())
             if not any(label == lbl for _, lbl in _LABELS)]
    if not labels:
        sys.exit(0)  # nothing recognizable — stay silent

    note = (
        "[Clawness] Detected project stack (heuristic from project files): "
        + ", ".join(labels + extra)
        + ". Apply these ecosystems' current conventions and idioms by default, "
        "and prefer their up-to-date best practices."
    )
    if versions:
        note += (
            " Write code for the MAJOR VERSIONS shown, not the newest release you "
            "know of — check the manifest before using an API you believe is current."
        )
    note += " Correct this if the codebase says otherwise. Silence with CLAW_NO_STACK_NOTE=1."
    print(note)

    # --- Corpus staleness (independent of the stack note above) ---
    # Rides this hook because the versions it needs are already parsed here.
    # CLAUDE.md is explicit that `scan_project` re-runs uncached on every prompt,
    # so version work must not move onto that path — SessionStart only.
    staleness_note = check_staleness(cwd_path, versions)
    if staleness_note:
        print("\n" + staleness_note)
    sys.exit(0)


def check_staleness(cwd_path: Path, versions: dict) -> str:
    """The staleness note for this project, or "" — never raises.

    Reads stamps from the global corpus AND the project's own `.clawness/rules/`,
    project winning by id (the same override precedence `add_rules` applies).
    That last part is what makes rules written by `/clawness:refresh` subject to
    the very check that produced them: stamped at the major they were checked
    against, they go stale when the project moves again. Without it, generated
    rules would be the one class that can never be detected as stale.
    """
    if os.environ.get("CLAW_NO_STALENESS_NOTE") or not versions:
        return ""
    root = git_root(cwd_path)
    if root is None:
        return ""
    try:
        from clawness.core import _replace_by_id, load_rules
        from clawness.staleness import render_note, stale_rules, summarize, unasked

        global_dir = Path(os.environ.get("CLAW_RULES_DIR") or
                          Path(__file__).resolve().parent.parent / "rules")
        ranked, mandatory = load_rules(global_dir)
        project_dir = root / ".clawness" / "rules"
        if project_dir.is_dir():
            proj_ranked, proj_mandatory = load_rules(project_dir)
            ranked = _replace_by_id(ranked, proj_ranked)
            mandatory = mandatory + proj_mandatory

        summaries = summarize(stale_rules(ranked + mandatory, versions))
        # `unasked` is called LAST, and records as it goes, so a session that
        # would have stayed quiet for any reason above doesn't spend the one shot.
        return render_note(unasked(root, summaries))
    except Exception:
        return ""


if __name__ == "__main__":
    main()
