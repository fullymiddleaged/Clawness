#!/usr/bin/env bash
set -euo pipefail

# -- Clawness installer (macOS / Linux) --------------
#
# Run from the clawness directory:
#     bash install.sh
#
# Options:
#     --no-semantic     Skip model2vec; lexical + concept retrieval only
#     --skip-hook       Don't configure Claude Code settings.json
#     --settings PATH   Use a custom settings.json path
#     --dry-run         Show what would be written without changing anything
# ----------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SCRIPT_DIR/rules"
HOOK_SCRIPT="$SCRIPT_DIR/hooks/claude_hook.py"
SETUP_PY="$SCRIPT_DIR/hooks/setup_settings.py"
CORE_MODULE="$SCRIPT_DIR/writ_lite/core.py"

SKIP_HOOK=false
SETTINGS_PATH=""
DRY_RUN=""
SEMANTIC=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --semantic|--with-semantic) SEMANTIC=true; shift ;;
        --no-semantic) SEMANTIC=false; shift ;;
        --skip-hook)   SKIP_HOOK=true; shift ;;
        --settings)    if [ -z "${2:-}" ]; then echo "ERROR: --settings requires a path"; exit 1; fi; SETTINGS_PATH="$2"; shift 2 ;;
        --dry-run)     DRY_RUN="--dry-run"; shift ;;
        *)             echo "Unknown option: $1"; exit 1 ;;
    esac
done

# -- banner -----------------------------
echo ""
echo "  +==================================+"
echo "  |        Clawness Installer        |"
echo "  |   lightweight rule retrieval for   |"
echo "  |       AI coding agents             |"
echo "  +==================================+"
echo ""

# -- step 1: check python ---------------------
echo "[1/7] Checking Python..."

PY_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 10 ]; then
            PY_CMD="$candidate"
            break
        fi
    fi
done

if [ -z "$PY_CMD" ]; then
    echo "  ERROR: Python 3.10+ not found."
    echo "  Install it via your package manager or https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$("$PY_CMD" --version 2>&1)
echo "  ✓ $PY_VERSION (command: $PY_CMD)"

# -- step 2: install clawness + dependencies ----------------------
echo ""
echo "[2/7] Installing clawness + dependencies..."
echo "  Installs the 'clawness' command + PyYAML into your Python ($PY_CMD)."
echo "  Downloads from PyPI — this can take a minute."

# Editable install so `clawness` and `python -m writ_lite.cli` work from any
# directory, while rules keep loading from this folder. PyYAML comes as a
# dependency; the [semantic] extra (next) adds model2vec + numpy.
"$PY_CMD" -m pip install -e "$SCRIPT_DIR" --user 2>&1 | sed 's/^/    /' || \
    "$PY_CMD" -m pip install -e "$SCRIPT_DIR" --user --break-system-packages 2>&1 | sed 's/^/    /' || true
if ! "$PY_CMD" -c 'import yaml' 2>/dev/null; then
    echo "  ERROR: Failed to install clawness (PyYAML not importable)."
    echo "  Try running manually: $PY_CMD -m pip install -e \"$SCRIPT_DIR\" --user"
    exit 1
fi
echo "  ✓ clawness installed — 'clawness' command available (PyYAML ready)"

# Semantic (model2vec) embeddings — ON by default; skip with --no-semantic.
# Best-effort: a failed install/download falls back to lexical + concepts.
SEMANTIC_OK=false
if [ "$SEMANTIC" = true ]; then
    echo "  Semantic retrieval requested — installing model2vec + numpy (~50 MB, may take a few minutes)..."
    "$PY_CMD" -m pip install -e "$SCRIPT_DIR[semantic]" --user 2>&1 || \
        "$PY_CMD" -m pip install -e "$SCRIPT_DIR[semantic]" --user --break-system-packages 2>&1 || true
    if "$PY_CMD" -c 'import model2vec' 2>/dev/null; then
        SEMANTIC_OK=true
        echo "  ✓ model2vec installed"
        echo "  Pre-downloading static embedding model (first time only)..."
        ( cd "$SCRIPT_DIR" && "$PY_CMD" -c 'from writ_lite.embeddings import get_default_embedder as g; e=g(); print("  ✓ semantic model ready (" + e.name + ")") if e else print("  ⚠ model load failed — retrieval falls back to lexical until available")' ) || \
            echo "  ⚠ Model download failed (offline?) — semantic falls back to lexical until the model is available"
    else
        echo "  ⚠ Could not install model2vec — continuing with lexical + concept retrieval"
    fi
else
    echo "  (semantic disabled via --no-semantic — using lexical + concept retrieval)"
fi

# -- step 3: verify files ------------------------------------------
echo ""
echo "[3/7] Verifying files..."

MISSING=0
for f in "$CORE_MODULE" "$HOOK_SCRIPT" "$SETUP_PY"; do
    if [ ! -f "$f" ]; then
        echo "  MISSING: $f"
        MISSING=1
    fi
done
if [ ! -d "$RULES_DIR" ]; then
    echo "  MISSING: $RULES_DIR/"
    MISSING=1
fi

if [ "$MISSING" -eq 1 ]; then
    echo "  ERROR: Run this script from inside the clawness directory."
    exit 1
fi

RULE_COUNT=$(find "$RULES_DIR" -name "*.yml" | wc -l | tr -d ' ')
echo "  ✓ $RULE_COUNT rule files found"

# -- step 4: lint rules ----------------------
echo ""
echo "[4/7] Linting rules..."

cd "$SCRIPT_DIR"
LINT_OUTPUT=$("$PY_CMD" -m writ_lite.cli --rules-dir "$RULES_DIR" lint 2>&1) && LINT_OK=true || LINT_OK=false

if [ "$LINT_OK" = true ]; then
    echo "  ✓ $LINT_OUTPUT"
else
    echo "  ⚠ Some rules have issues:"
    echo "$LINT_OUTPUT" | sed 's/^/    /'
fi

# -- step 5: test retrieval --------------------
echo ""
echo "[5/7] Testing retrieval..."

TEST_OUTPUT=$("$PY_CMD" -m writ_lite.cli --rules-dir "$RULES_DIR" query "implement async REST endpoint with error handling" 2>&1) || {
    echo "  ERROR: Retrieval failed:"
    echo "$TEST_OUTPUT" | sed 's/^/    /'
    exit 1
}

# Parse first line for stats
FIRST_LINE=$(echo "$TEST_OUTPUT" | head -1)
if echo "$FIRST_LINE" | grep -qE '[0-9]+ rules.*[0-9.]+ms'; then
    N_RULES=$(echo "$FIRST_LINE" | grep -oE '[0-9]+ rules' | grep -oE '[0-9]+')
    MS=$(echo "$FIRST_LINE" | grep -oE '[0-9.]+ms' | head -1)
    echo "  ✓ Retrieved $N_RULES rules in $MS"
else
    echo "  ✓ Retrieval working"
fi

# -- step 6: install agents & skills --------------------------------
echo ""
echo "[6/7] Installing agents & skills..."

AGENTS_DIR="$SCRIPT_DIR/agents"
SETUP_AGENTS="$SCRIPT_DIR/hooks/setup_agents.py"
SKILLS_DIR="$SCRIPT_DIR/skills"
SETUP_SKILLS="$SCRIPT_DIR/hooks/setup_skills.py"

if [ -d "$AGENTS_DIR" ] && [ -f "$SETUP_AGENTS" ]; then
    AGENT_RESULT=$("$PY_CMD" "$SETUP_AGENTS" "$AGENTS_DIR" 2>&1)
    echo "  Agents: $AGENT_RESULT"
fi

if [ -d "$SKILLS_DIR" ] && [ -f "$SETUP_SKILLS" ]; then
    SKILL_RESULT=$("$PY_CMD" "$SETUP_SKILLS" "$SKILLS_DIR" 2>&1)
    echo "  Skills: $SKILL_RESULT"
fi

# -- step 7: configure hook ----------------------------------------
echo ""

if [ "$SKIP_HOOK" = true ]; then
    echo "[7/7] Skipping hook setup (--skip-hook)"
else
    echo "[7/7] Configuring Claude Code hook..."

    SETUP_ARGS=("$SETUP_PY" "$HOOK_SCRIPT")
    if [ -n "$SETTINGS_PATH" ]; then
        SETUP_ARGS+=("--settings" "$SETTINGS_PATH")
    fi
    if [ -n "$DRY_RUN" ]; then
        SETUP_ARGS+=("$DRY_RUN")
    fi

    HOOK_RESULT=$("$PY_CMD" "${SETUP_ARGS[@]}" 2>&1) && HOOK_OK=true || HOOK_OK=false

    if [ "$HOOK_OK" = true ]; then
        echo "  ✓ $HOOK_RESULT"
    else
        echo "  ✗ $HOOK_RESULT"
        echo ""
        echo "  To configure manually, add this to ~/.claude/settings.json:"
        echo ""
        echo '  {'
        echo '    "hooks": {'
        echo '      "UserPromptSubmit": [{'
        echo '        "hooks": [{'
        echo '          "type": "command",'
        echo "          \"command\": \"$PY_CMD \\\"$HOOK_SCRIPT\\\"\","
        echo '          "timeout": 5'
        echo '        }]'
        echo '      }]'
        echo '    }'
        echo '  }'
    fi
fi

# -- done ------------------------------
echo ""
echo "  ===================================="
echo "  Clawness is ready."
echo "  ===================================="
echo ""
echo "  Usage:"
echo "    clawness query \"your task here\""
echo "    clawness stats"
echo "    clawness plan status"
echo ""
echo "  If 'clawness' isn't found, your Python user-scripts dir isn't on PATH —"
echo "  use '$PY_CMD -m writ_lite.cli ...' instead (identical, works from anywhere)."
echo ""
echo "  Add rules:  drop .yml files into $RULES_DIR/<domain>/"
if [ "$SEMANTIC_OK" = true ]; then
    echo "  Semantic:   model2vec embeddings enabled (run 'stats' to confirm)"
elif [ "$SEMANTIC" = true ]; then
    echo "  Semantic:   requested, but model2vec couldn't be installed — using lexical + concepts"
else
    echo "  Semantic:   off (lexical + concepts) — you passed --no-semantic"
fi
echo "  Uninstall:  run 'bash $SCRIPT_DIR/uninstall.sh', then delete the folder"
echo ""
