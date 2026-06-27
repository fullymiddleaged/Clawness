#!/usr/bin/env bash
# ----------------------------------
# Clawness uninstaller (manual install)
#
# Reverses what install.sh did OUTSIDE this folder, so deleting the folder
# afterwards is safe:
#   1. Removes Writ hooks from ~/.claude/settings.json (otherwise they dangle
#      and error on every prompt once the folder is gone).
#   2. Removes the agent files copied to ~/.claude/agents/.
#   3. Removes the skill folders copied to ~/.claude/skills/.
#   4. Removes the embeddings cache (~/.cache/clawness).
#   5. Uninstalls the clawness pip package (the editable install would otherwise
#      dangle once the folder is gone).
#
# Plugin users: don't use this — run `claude plugin uninstall clawness` instead.
# ----------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"   # matches the installer (honors CLAUDE_CONFIG_DIR)
AGENTS_DIR="$CLAUDE_DIR/agents"
SKILLS_DIR="$CLAUDE_DIR/skills"
CACHE_DIR="${WRIT_CACHE_DIR:-$HOME/.cache/clawness}"

PY_CMD=""
for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1; then PY_CMD="$c"; break; fi
done

echo "Clawness uninstaller"
echo ""

# 1. settings.json hooks
echo "[1/5] Removing hooks from settings.json..."
if [ -n "$PY_CMD" ] && [ -f "$SCRIPT_DIR/hooks/setup_settings.py" ]; then
    "$PY_CMD" "$SCRIPT_DIR/hooks/setup_settings.py" --uninstall || \
        echo "  (could not edit settings.json automatically — remove entries referencing clawness/hooks/*.py by hand)"
else
    echo "  No Python found — edit $CLAUDE_DIR/settings.json and remove entries referencing clawness/hooks/*.py"
fi

# 2. agents
echo "[2/5] Removing agents from $AGENTS_DIR..."
if [ -d "$SCRIPT_DIR/agents" ] && [ -d "$AGENTS_DIR" ]; then
    for f in "$SCRIPT_DIR"/agents/*.md; do
        [ -e "$f" ] || continue
        name="$(basename "$f")"
        if [ -f "$AGENTS_DIR/$name" ]; then
            rm -f "$AGENTS_DIR/$name" && echo "  removed agent: $name"
        fi
    done
fi

# 3. skills
echo "[3/5] Removing skills from $SKILLS_DIR..."
if [ -d "$SCRIPT_DIR/skills" ] && [ -d "$SKILLS_DIR" ]; then
    for d in "$SCRIPT_DIR"/skills/*/; do
        [ -d "$d" ] || continue
        name="$(basename "$d")"
        if [ -d "$SKILLS_DIR/$name" ]; then
            rm -rf "$SKILLS_DIR/$name" && echo "  removed skill: $name"
        fi
    done
fi

# 4. cache
echo "[4/5] Removing embeddings cache..."
if [ -d "$CACHE_DIR" ]; then
    rm -rf "$CACHE_DIR" && echo "  removed $CACHE_DIR"
else
    echo "  (none)"
fi

# 5. pip package
echo "[5/5] Uninstalling the clawness pip package..."
if [ -n "$PY_CMD" ]; then
    "$PY_CMD" -m pip uninstall -y clawness >/dev/null 2>&1 && echo "  removed clawness package" || \
        echo "  (not pip-installed, or already removed)"
else
    echo "  No Python found — run: pip uninstall clawness"
fi

echo ""
echo "Left in place on purpose:"
echo "  - Python packages (pyyaml, model2vec, numpy) — shared with other tools."
echo "    Remove if you want: $PY_CMD -m pip uninstall model2vec numpy"
echo "  - The model2vec model cache in ~/.cache/huggingface (reusable downloads)."
echo "  - Per-project rules and state in each project's .writ/ (your data)."
echo ""
echo "Finally, delete this folder to finish:"
echo "  rm -rf \"$SCRIPT_DIR\""
