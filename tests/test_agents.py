"""
Tests for the shipped agent definitions (agents/*.md).

Runs under pytest, or standalone:  python tests/test_agents.py
"""

import re
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent / "agents"


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    assert m, f"{path.name} has no frontmatter"
    return m.group(1)


def test_every_shipped_agent_inherits_the_session_model():
    # A pinned alias routes away from the user's model: on a gateway serving GLM,
    # `model: sonnet` quietly sends that agent's work to Claude Sonnet instead.
    # Omitting `model:` is Claude Code's `inherit` default.
    agents = sorted(AGENTS.glob("*.md"))
    assert agents
    for path in agents:
        pinned = re.search(r"^model:\s*(\S+)", _frontmatter(path), re.M)
        assert not pinned or pinned.group(1) == "inherit", (
            f"{path.name} pins model: {pinned.group(1)}")


if __name__ == "__main__":
    test_every_shipped_agent_inherits_the_session_model()
    print("ok")
    sys.exit(0)
