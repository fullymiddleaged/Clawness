<#
.SYNOPSIS
    Clawness installer for Windows.

.DESCRIPTION
    Checks prerequisites, validates the rule corpus, configures the
    Claude Code hook in ~/.claude/settings.json, and runs a test query.

    Run from the clawness directory:
        powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

param(
    [Alias('WritDir')]      # back-compat: the old parameter name
    [string]$ClawnessDir = '',
    [string]$SettingsPath = '',
    [switch]$SkipHook,
    [switch]$DryRun,        # show what would be written without changing settings.json
    [switch]$Semantic,      # removed; accepted as a no-op for back-compat
    [switch]$NoSemantic     # removed; accepted as a no-op for back-compat
)

# NOTE: We intentionally use 'Continue', not 'Stop'.
# 'Stop' treats ANY stderr output from native commands as a fatal error,
# which kills the script when Python prints a traceback or even a warning.
$ErrorActionPreference = 'Continue'

# -- resolve paths --------------------------------------------------
if (-not $ClawnessDir) {
    $ClawnessDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$ClawnessDir = (Resolve-Path $ClawnessDir).Path

$RulesDir   = Join-Path $ClawnessDir 'rules'
$HookScript = Join-Path $ClawnessDir 'hooks\claude_hook.py'
$SetupPy    = Join-Path $ClawnessDir 'hooks\setup_settings.py'
$CoreModule = Join-Path $ClawnessDir 'clawness\core.py'

# -- banner ---------------------------------------------------------
Write-Host ''
Write-Host '  ======================================' -ForegroundColor Cyan
Write-Host '         Clawness Installer'             -ForegroundColor Cyan
Write-Host '    lightweight rule retrieval for'        -ForegroundColor Cyan
Write-Host '        AI coding agents'                  -ForegroundColor Cyan
Write-Host '  ======================================' -ForegroundColor Cyan
Write-Host ''

# -- step 1: check python ------------------------------------------
Write-Host '[1/7] Checking Python...' -ForegroundColor Yellow

$pyCmd = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    try {
        $ver = & $candidate --version 2>&1
        $verStr = [string]$ver
        if ($verStr -match 'Python (\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 10) {
                $pyCmd = $candidate
                break
            }
        }
    } catch { }
}

if (-not $pyCmd) {
    Write-Host '  ERROR: Python 3.10+ not found.' -ForegroundColor Red
    Write-Host '  Install from https://www.python.org/downloads/' -ForegroundColor Red
    exit 1
}

$pyVersion = & $pyCmd --version 2>&1
Write-Host ('  OK: ' + $pyVersion + ' (command: ' + $pyCmd + ')') -ForegroundColor Green

# -- step 2: install clawness + dependencies -----------------------
Write-Host ''
Write-Host '[2/7] Installing clawness + dependencies...' -ForegroundColor Yellow
Write-Host ("  Installs the 'clawness' command + PyYAML into your Python (" + $pyCmd + ').') -ForegroundColor Gray
Write-Host '  Downloads from PyPI - this can take a minute.' -ForegroundColor Gray

# Editable install so `clawness` and `python -m clawness.cli` work from any
# directory, while rules keep loading from this folder. PyYAML (the only
# dependency) comes along automatically.
#
# Try a plain install first (works inside a venv and user-writable Pythons),
# then --user (avoids needing admin on a system-wide Python — pip rejects
# --user inside a venv, so plain must come first), then add
# --break-system-packages (Debian / PEP 668).
$flagSets = @('', '--user', '--user --break-system-packages')
foreach ($fs in $flagSets) {
    $flags = if ($fs) { $fs -split ' ' } else { @() }
    & $pyCmd -m pip install -e $ClawnessDir @flags 2>&1 | Out-Null
    & $pyCmd -c 'import yaml' 2>$null
    if ($LASTEXITCODE -eq 0) { break }
}
if ($LASTEXITCODE -ne 0) {
    Write-Host '  ERROR: Failed to install clawness (PyYAML not importable).' -ForegroundColor Red
    Write-Host ('  Try manually: ' + $pyCmd + ' -m pip install -e "' + $ClawnessDir + '"') -ForegroundColor Red
    exit 1
}
Write-Host "  OK: clawness installed - 'clawness' command available (PyYAML ready)" -ForegroundColor Green

# -- step 3: verify files ------------------------------------------
Write-Host ''
Write-Host '[3/7] Verifying files...' -ForegroundColor Yellow

$missing = @()
foreach ($f in @($CoreModule, $HookScript, $SetupPy)) {
    if (-not (Test-Path $f)) { $missing += $f }
}
if (-not (Test-Path $RulesDir)) { $missing += $RulesDir }

if ($missing.Count -gt 0) {
    Write-Host '  ERROR: Missing files:' -ForegroundColor Red
    foreach ($m in $missing) {
        Write-Host ('    - ' + $m) -ForegroundColor Red
    }
    Write-Host '  Run this script from inside the clawness directory.' -ForegroundColor Red
    exit 1
}

$ruleCount = (Get-ChildItem -Path $RulesDir -Recurse -Filter '*.yml').Count
Write-Host ('  OK: ' + $ruleCount + ' rule files found') -ForegroundColor Green

# -- step 4: lint rules --------------------------------------------
Write-Host ''
Write-Host '[4/7] Linting rules...' -ForegroundColor Yellow

& $pyCmd -m clawness.cli --rules-dir $RulesDir lint 2>&1 | Tee-Object -Variable lintOutput | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  WARNING: Some rules have issues:' -ForegroundColor DarkYellow
    Write-Host $lintOutput
} else {
    Write-Host ('  OK: ' + $lintOutput) -ForegroundColor Green
}

# -- step 5: test retrieval ----------------------------------------
Write-Host ''
Write-Host '[5/7] Testing retrieval...' -ForegroundColor Yellow

$testQuery = 'implement async REST endpoint with error handling'
& $pyCmd -m clawness.cli --rules-dir $RulesDir query $testQuery 2>&1 | Tee-Object -Variable result | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host '  ERROR: Retrieval failed:' -ForegroundColor Red
    Write-Host $result
    exit 1
}

$firstLine = ($result | Select-Object -First 1)
if ($firstLine -match '(\d+) rules.*?(\d+\.\d+)ms') {
    Write-Host ('  OK: Retrieved ' + $Matches[1] + ' rules in ' + $Matches[2] + 'ms') -ForegroundColor Green
} else {
    Write-Host '  OK: Retrieval working' -ForegroundColor Green
}

# -- step 6: install agents & skills --------------------------------
Write-Host ''
Write-Host '[6/7] Installing agents & skills...' -ForegroundColor Yellow

$AgentsDir = Join-Path $ClawnessDir 'agents'
$SetupAgents = Join-Path $ClawnessDir 'hooks\setup_agents.py'
$SkillsDir = Join-Path $ClawnessDir 'skills'
$SetupSkills = Join-Path $ClawnessDir 'hooks\setup_skills.py'

if ((Test-Path $AgentsDir) -and (Test-Path $SetupAgents)) {
    & $pyCmd $SetupAgents $AgentsDir 2>&1 | Tee-Object -Variable agentResult | Out-Null
    Write-Host ('  Agents: ' + $agentResult) -ForegroundColor Green
}

if ((Test-Path $SkillsDir) -and (Test-Path $SetupSkills)) {
    & $pyCmd $SetupSkills $SkillsDir 2>&1 | Tee-Object -Variable skillResult | Out-Null
    Write-Host ('  Skills: ' + $skillResult) -ForegroundColor Green
}

# -- step 7: configure hook ----------------------------------------
Write-Host ''

if ($SkipHook) {
    Write-Host '[7/7] Skipping hook setup' -ForegroundColor DarkYellow
} else {
    Write-Host '[7/7] Configuring Claude Code hook...' -ForegroundColor Yellow

    $setupArgs = @($SetupPy, $HookScript)
    if ($SettingsPath) {
        $setupArgs += @('--settings', $SettingsPath)
    }
    if ($DryRun) {
        $setupArgs += '--dry-run'
    }

    & $pyCmd @setupArgs 2>&1 | Tee-Object -Variable hookResult | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Write-Host ('  ' + $hookResult) -ForegroundColor Red
        Write-Host ''
        Write-Host '  To configure manually, add this to ~/.claude/settings.json' -ForegroundColor DarkYellow
        Write-Host '  (this is only the rule-retrieval hook — re-run the installer to wire' -ForegroundColor DarkYellow
        Write-Host '   the full set including the plan gate and access guard):' -ForegroundColor DarkYellow
        Write-Host ''
        # Portable interpreter picker + the real 30s timeout, matching what
        # setup_settings.py / plugin.json write (NOT a single hardcoded interpreter).
        $hookPath = $HookScript -replace '\\', '/'
        $picker = 'for p in python3 python py; do command -v \"$p\" >/dev/null 2>&1 && exec \"$p\" \"' + $hookPath + '\"; done'
        Write-Host '  {'                                          -ForegroundColor Gray
        Write-Host '    "hooks": {'                               -ForegroundColor Gray
        Write-Host '      "UserPromptSubmit": [{'                 -ForegroundColor Gray
        Write-Host '        "hooks": [{'                          -ForegroundColor Gray
        Write-Host '          "type": "command",'                 -ForegroundColor Gray
        Write-Host ('          "command": "' + $picker + '",')    -ForegroundColor Gray
        Write-Host '          "timeout": 30'                      -ForegroundColor Gray
        Write-Host '        }]'                                   -ForegroundColor Gray
        Write-Host '      }]'                                     -ForegroundColor Gray
        Write-Host '    }'                                        -ForegroundColor Gray
        Write-Host '  }'                                          -ForegroundColor Gray
    } else {
        Write-Host ('  ' + $hookResult) -ForegroundColor Green
    }
}

# -- done ----------------------------------------------------------
Write-Host ''
Write-Host '  ======================================' -ForegroundColor Cyan
Write-Host '  Clawness is ready.'                     -ForegroundColor Cyan
Write-Host '  ======================================' -ForegroundColor Cyan
Write-Host ''
Write-Host '  Usage:' -ForegroundColor White
Write-Host '    clawness query "your task here"' -ForegroundColor Gray
Write-Host '    clawness stats'                  -ForegroundColor Gray
Write-Host '    clawness plan status'            -ForegroundColor Gray
Write-Host ''
Write-Host "  If 'clawness' isn't found, your Python user-scripts dir isn't on PATH -" -ForegroundColor White
Write-Host ('  use ' + $pyCmd + ' -m clawness.cli ... instead (identical, works anywhere).') -ForegroundColor Gray
Write-Host ''
Write-Host ('  Add rules:  drop .yml files into ' + $RulesDir + '\<domain>\') -ForegroundColor White
Write-Host '  Retrieval:  BM25 + TF-IDF + RRF + concept expansion (pure Python, ~1ms)' -ForegroundColor White
Write-Host ('  Uninstall:  run .\uninstall.ps1 in ' + $ClawnessDir + ', then delete the folder') -ForegroundColor White
Write-Host ''
