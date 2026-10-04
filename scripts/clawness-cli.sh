#!/bin/sh
# Run the bundled `clawness` CLI from any directory.
#
# Skills reach this as "${CLAUDE_PLUGIN_ROOT}/scripts/clawness-cli.sh": Claude Code
# substitutes that reference in a SKILL.md body at load time. The plugin never
# pip-installs `clawness` (only pyyaml), so the package is found by putting the
# plugin root, located from this script's own path, on PYTHONPATH.
#
# Claude Code versions that predate the substitution leave the reference literal,
# and the Bash tool has no CLAUDE_PLUGIN_ROOT in its environment, so it expands to
# an empty path. Skills therefore fall back to the per-session wrapper that
# hooks/ensure_deps.py stashes in <config>/clawness/ (see CC-PLUGIN-PATH-001).

here=$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })
# `pwd -W` (Git Bash on Windows) gives C:/... — Windows Python can't read the
# /c/... form plain `pwd` returns there. Elsewhere it fails and `pwd` answers.

if [ -n "$CLAWNESS_CLI_PRINT_ROOT" ]; then
    printf '%s\n' "$here"
    exit 0
fi

export PYTHONPATH="$here"
for p in python3 python py; do
    command -v "$p" >/dev/null 2>&1 && exec "$p" -m clawness.cli "$@"
done
echo "clawness: no python on PATH" >&2
exit 1
