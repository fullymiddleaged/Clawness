# ----------------------------------
# Clawness uninstaller (manual install)
#
# Reverses what install.ps1 did OUTSIDE this folder, so deleting the folder
# afterwards is safe:
#   1. Removes Writ hooks from settings.json (otherwise they dangle and error
#      on every prompt once the folder is gone).
#   2. Removes the agent files copied to ~/.claude/agents/.
#   3. Removes the skill folders copied to ~/.claude/skills/.
#   4. Removes the embeddings cache (~/.cache/clawness).
#   5. Uninstalls the clawness pip package (the editable install would otherwise
#      dangle once the folder is gone).
#
# Plugin users: don't use this - run `claude plugin uninstall clawness` instead.
# ----------------------------------
$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$AgentsDir = Join-Path $ClaudeDir 'agents'
$SkillsDir = Join-Path $ClaudeDir 'skills'
$CacheDir  = if ($env:WRIT_CACHE_DIR) { $env:WRIT_CACHE_DIR } else { Join-Path $env:USERPROFILE '.cache\clawness' }

$pyCmd = $null
foreach ($c in @('python', 'python3', 'py')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $pyCmd = $c; break }
}

Write-Host 'Clawness uninstaller'
Write-Host ''

# 1. settings.json hooks
Write-Host '[1/5] Removing hooks from settings.json...'
$setup = Join-Path $ScriptDir 'hooks\setup_settings.py'
if ($pyCmd -and (Test-Path $setup)) {
    & $pyCmd $setup --uninstall
} else {
    Write-Host "  No Python found - edit $ClaudeDir\settings.json and remove entries referencing clawness\hooks\*.py"
}

# 2. agents
Write-Host "[2/5] Removing agents from $AgentsDir..."
$srcAgents = Join-Path $ScriptDir 'agents'
if ((Test-Path $srcAgents) -and (Test-Path $AgentsDir)) {
    Get-ChildItem -Path $srcAgents -Filter *.md | ForEach-Object {
        $target = Join-Path $AgentsDir $_.Name
        if (Test-Path $target) { Remove-Item $target -Force; Write-Host "  removed agent: $($_.Name)" }
    }
}

# 3. skills
Write-Host "[3/5] Removing skills from $SkillsDir..."
$srcSkills = Join-Path $ScriptDir 'skills'
if ((Test-Path $srcSkills) -and (Test-Path $SkillsDir)) {
    Get-ChildItem -Path $srcSkills -Directory | ForEach-Object {
        $target = Join-Path $SkillsDir $_.Name
        if (Test-Path $target) { Remove-Item $target -Recurse -Force; Write-Host "  removed skill: $($_.Name)" }
    }
}

# 4. cache
Write-Host '[4/5] Removing embeddings cache...'
if (Test-Path $CacheDir) { Remove-Item $CacheDir -Recurse -Force; Write-Host "  removed $CacheDir" }
else { Write-Host '  (none)' }

# 5. pip package
Write-Host '[5/5] Uninstalling the clawness pip package...'
if ($pyCmd) {
    & $pyCmd -m pip uninstall -y clawness 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host '  removed clawness package' }
    else { Write-Host '  (not pip-installed, or already removed)' }
} else {
    Write-Host '  No Python found - run: pip uninstall clawness'
}

Write-Host ''
Write-Host 'Left in place on purpose:'
Write-Host '  - Python packages (pyyaml, model2vec, numpy) - shared with other tools.'
Write-Host "    Remove if you want: $pyCmd -m pip uninstall model2vec numpy"
Write-Host '  - The model2vec model cache in ~/.cache/huggingface (reusable downloads).'
Write-Host '  - Per-project rules and state in each project .writ/ (your data).'
Write-Host ''
Write-Host 'Finally, delete this folder to finish:'
Write-Host ('  Remove-Item -Recurse -Force "' + $ScriptDir + '"')
