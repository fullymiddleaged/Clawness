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
    [string]$WritDir = '',
    [string]$SettingsPath = '',
    [switch]$SkipHook,
    [switch]$NoSemantic
)

$Semantic = -not $NoSemantic

# NOTE: We intentionally use 'Continue', not 'Stop'.
# 'Stop' treats ANY stderr output from native commands as a fatal error,
# which kills the script when Python prints a traceback or even a warning.
$ErrorActionPreference = 'Continue'

# -- resolve paths --------------------------------------------------
if (-not $WritDir) {
    $WritDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$WritDir = (Resolve-Path $WritDir).Path

$RulesDir   = Join-Path $WritDir 'rules'
$HookScript = Join-Path $WritDir 'hooks\claude_hook.py'
$SetupPy    = Join-Path $WritDir 'hooks\setup_settings.py'
$CoreModule = Join-Path $WritDir 'writ_lite\core.py'

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

# Editable install so `clawness` and `python -m writ_lite.cli` work from any
# directory, while rules keep loading from this folder. PyYAML comes as a
# dependency; the [semantic] extra (next) adds model2vec + numpy.
& $pyCmd -m pip install -e $WritDir --user 2>&1 | Out-Null
& $pyCmd -c 'import yaml' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $pyCmd -m pip install -e $WritDir --user --break-system-packages 2>&1 | Out-Null
    & $pyCmd -c 'import yaml' 2>$null
}
if ($LASTEXITCODE -ne 0) {
    Write-Host '  ERROR: Failed to install clawness (PyYAML not importable).' -ForegroundColor Red
    Write-Host ('  Try manually: ' + $pyCmd + ' -m pip install -e "' + $WritDir + '" --user') -ForegroundColor Red
    exit 1
}
Write-Host "  OK: clawness installed - 'clawness' command available (PyYAML ready)" -ForegroundColor Green

# Semantic (model2vec) embeddings - ON by default; skip with -NoSemantic.
$SemanticOk = $false
if ($Semantic) {
    Write-Host '  Semantic retrieval requested - installing model2vec + numpy (~50 MB, may take a few minutes)...' -ForegroundColor DarkYellow
    $pkgSpec = "$WritDir[semantic]"
    & $pyCmd -m pip install -e $pkgSpec --user 2>&1 | Out-Null
    & $pyCmd -c 'import model2vec' 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $pyCmd -m pip install -e $pkgSpec --user --break-system-packages 2>&1 | Out-Null
        & $pyCmd -c 'import model2vec' 2>$null
    }
    if ($LASTEXITCODE -eq 0) {
        $SemanticOk = $true
        Write-Host '  OK: model2vec installed' -ForegroundColor Green
        Write-Host '  Pre-downloading static embedding model (first time only)...' -ForegroundColor DarkYellow
        # Run the prewarm from a temp script file (here-string) rather than an
        # inline `-c`: PowerShell mangles complex inline Python arguments. The
        # file lives in $WritDir so `import writ_lite` resolves.
        $prewarmCode = @'
try:
    from writ_lite.embeddings import get_default_embedder
    e = get_default_embedder()
    if e:
        print("  OK: semantic model ready (" + e.name + ")")
    else:
        print("  WARN: model load failed - falls back to lexical")
except Exception:
    print("  WARN: model prewarm skipped - falls back to lexical")
'@
        $prewarmFile = Join-Path $WritDir '.writ_prewarm.py'
        try {
            Set-Content -Path $prewarmFile -Value $prewarmCode -Encoding ASCII
            Push-Location $WritDir
            & $pyCmd $prewarmFile
            Pop-Location
        } catch {
            Write-Host '  WARN: model prewarm skipped - falls back to lexical' -ForegroundColor DarkYellow
        } finally {
            Remove-Item $prewarmFile -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host '  WARN: Could not install model2vec - continuing with lexical + concept retrieval' -ForegroundColor DarkYellow
    }
} else {
    Write-Host '  (semantic disabled via -NoSemantic - using lexical + concept retrieval)' -ForegroundColor Gray
}

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

& $pyCmd -m writ_lite.cli --rules-dir $RulesDir lint 2>&1 | Tee-Object -Variable lintOutput | Out-Null
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
& $pyCmd -m writ_lite.cli --rules-dir $RulesDir query $testQuery 2>&1 | Tee-Object -Variable result | Out-Null

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

$AgentsDir = Join-Path $WritDir 'agents'
$SetupAgents = Join-Path $WritDir 'hooks\setup_agents.py'
$SkillsDir = Join-Path $WritDir 'skills'
$SetupSkills = Join-Path $WritDir 'hooks\setup_skills.py'

if (Test-Path $AgentsDir) {
    & $pyCmd $SetupAgents $AgentsDir 2>&1 | Tee-Object -Variable agentResult | Out-Null
    Write-Host ('  Agents: ' + $agentResult) -ForegroundColor Green
}

if (Test-Path $SkillsDir) {
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

    & $pyCmd @setupArgs 2>&1 | Tee-Object -Variable hookResult | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Write-Host ('  ' + $hookResult) -ForegroundColor Red
        Write-Host ''
        Write-Host '  To configure manually, add this to ~/.claude/settings.json:' -ForegroundColor DarkYellow
        Write-Host ''
        $hookPath = $HookScript -replace '\\', '/'
        Write-Host '  {'                                          -ForegroundColor Gray
        Write-Host '    "hooks": {'                               -ForegroundColor Gray
        Write-Host '      "UserPromptSubmit": [{'                 -ForegroundColor Gray
        Write-Host '        "hooks": [{'                          -ForegroundColor Gray
        Write-Host '          "type": "command",'                 -ForegroundColor Gray
        Write-Host ('          "command": "' + $pyCmd + ' \"' + $hookPath + '\"",' ) -ForegroundColor Gray
        Write-Host '          "timeout": 5'                       -ForegroundColor Gray
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
Write-Host ('  use ' + $pyCmd + ' -m writ_lite.cli ... instead (identical, works anywhere).') -ForegroundColor Gray
Write-Host ''
Write-Host ('  Add rules:  drop .yml files into ' + $RulesDir + '\<domain>\') -ForegroundColor White
if ($SemanticOk) {
    Write-Host '  Semantic:   model2vec embeddings enabled (run stats to confirm)' -ForegroundColor White
} elseif ($Semantic) {
    Write-Host '  Semantic:   requested, but model2vec could not be installed - using lexical + concepts' -ForegroundColor White
} else {
    Write-Host '  Semantic:   off (lexical + concepts) - you passed -NoSemantic' -ForegroundColor White
}
Write-Host ('  Uninstall:  run .\uninstall.ps1 in ' + $WritDir + ', then delete the folder') -ForegroundColor White
Write-Host ''
