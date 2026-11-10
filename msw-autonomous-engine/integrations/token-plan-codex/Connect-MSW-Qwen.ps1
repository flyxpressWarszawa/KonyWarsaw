#requires -Version 5.1
<#
.SYNOPSIS
  Opens interactive Qwen/Codex on a separate local copy of the existing MSW Render code.
.DESCRIPTION
  No API calls are made by this script. No keys are read from or written to files,
  no remote jobs are created, and no deployments or Git pushes are performed.
  The only remote action is Git fetching/cloning the owner's existing repository.
#>
[CmdletBinding()]
param([switch]$PrepareOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/flyxpressWarszawa/KonyWarsaw.git"
$baseBranch = "msw/engine-runtime-20261104"
$workBranch = "msw/codex-qwen-local-20261110"
$repoPath = Join-Path $HOME "MSW-Qwen\KonyWarsaw"
$workspace = Join-Path $repoPath "msw-autonomous-engine"
$envKey = "MSW_ALIYUN_TOKEN_PLAN_API_KEY"

function Invoke-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Git failed. Check the previous message and GitHub authentication."
    }
}

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw "Codex CLI is not available. Run codex --version in a new PowerShell session."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git for Windows is missing. Install with: winget install --id Git.Git -e --source winget (or https://git-scm.com/install/windows). Open a NEW PowerShell window and rerun the script."
}

# Keep original Codex desktop/GPT config untouched.
# Codex >=0.134 reads named profile ~/.codex/aliyun.config.toml.
# Provider is self-contained in that profile, not required in main config.toml.
$configPath = Join-Path $HOME ".codex\config.toml"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Original Codex config.toml is missing. Stop instead of replacing the GPT profile."
}
$profilePath = Join-Path $HOME ".codex\aliyun.config.toml"
$providerSection = @'
[model_providers.MSW_Aliyun_Token_Plan]
name = "Alibaba Cloud Token Plan - Singapore"
base_url = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
env_key = "MSW_ALIYUN_TOKEN_PLAN_API_KEY"
wire_api = "responses"
'@
if (-not (Test-Path -LiteralPath $profilePath)) {
    $profileText = @'
model = "qwen3.8-max"
model_provider = "MSW_Aliyun_Token_Plan"
model_reasoning_effort = "medium"
[mcp_servers.node_repl]
enabled = false
'@
    $profileText = $profileText.TrimEnd() + "`r`n`r`n" + $providerSection.TrimEnd() + "`r`n"
    [System.IO.File]::WriteAllText($profilePath, $profileText,
        (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Created separate keyless Qwen medium profile. GPT defaults not changed." -ForegroundColor Green
} else {
    $existing = Get-Content -LiteralPath $profilePath -Raw
    if ($existing -notmatch '(?m)^\s*model\s*=\s*"qwen3\.8-max"\s*$' -or
        $existing -notmatch '(?m)^\s*model_provider\s*=\s*"MSW_Aliyun_Token_Plan"\s*$') {
        throw "Existing aliyun.config.toml uses another model or provider. Do not overwrite it."
    }
    if ($existing -notmatch '(?m)^\s*\[model_providers\.MSW_Aliyun_Token_Plan\]\s*$') {
        $backup = $profilePath + ".backup-" + (Get-Date -Format "yyyyMMddHHmmss")
        Copy-Item -LiteralPath $profilePath -Destination $backup -ErrorAction Stop
        [System.IO.File]::AppendAllText($profilePath,
            ("`r`n" + $providerSection.TrimEnd() + "`r`n"),
            (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "[OK] Added provider to separate Qwen profile (backup saved)." -ForegroundColor Green
    } else {
        if ($existing -notmatch 'token-plan\.ap-southeast-1\.maas\.aliyuncs\.com/compatible-mode/v1' -or
            $existing -notmatch 'MSW_ALIYUN_TOKEN_PLAN_API_KEY') {
            throw "Existing Qwen profile has a different endpoint/key name. Review locally; no changes made."
        }
    }
}

$apiKey = [Environment]::GetEnvironmentVariable($envKey, "Process")
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    $apiKey = [Environment]::GetEnvironmentVariable($envKey, "User")
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "Token Plan key not found. Save the rotated key privately in the Windows USER environment variables; do not paste it here."
}
$env:MSW_ALIYUN_TOKEN_PLAN_API_KEY = $apiKey
Remove-Variable apiKey
Write-Host "[OK] Codex and the Alibaba profile are configured. Key value is not displayed." -ForegroundColor Green

if (-not (Test-Path -LiteralPath $repoPath)) {
    New-Item -Path (Split-Path -Path $repoPath -Parent) -ItemType Directory -Force | Out-Null
    Write-Host "Cloning only the existing MSW worker source branch..."
    # Download only one commit and the MSW worker folder; avoid full Git history.
    Invoke-Git -GitArgs @("clone", "--depth", "1", "--filter=blob:none", "--sparse", "--single-branch", "--branch", $baseBranch, $repoUrl, $repoPath)
    Invoke-Git -GitArgs @("-C", $repoPath, "sparse-checkout", "set", "msw-autonomous-engine")
}
if (-not (Test-Path -LiteralPath (Join-Path $repoPath ".git"))) {
    throw "Target folder exists but is not a Git repository. No files were changed."
}

$origin = (& git -C $repoPath remote get-url origin)
if ($LASTEXITCODE -ne 0 -or $origin -notmatch '^((https://|ssh://git@|git@)github\.com[:/])flyxpressWarszawa/KonyWarsaw(\.git)?$') {
    throw "The existing directory does not point at the authorized MSW GitHub repository."
}

$currentBranch = (& git -C $repoPath branch --show-current).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read local Git branch." }
if ($currentBranch -ne $workBranch) {
    $dirty = @(& git -C $repoPath status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw "Could not read worktree status." }
    if ($dirty.Count -gt 0) {
        throw "The repo has local changes. No branch switch performed; preserve your work."
    }
    $hasBranch = @(& git -C $repoPath branch --list $workBranch)
    if ($hasBranch.Count -gt 0) {
        Invoke-Git -GitArgs @("-C", $repoPath, "switch", $workBranch)
    } else {
        Invoke-Git -GitArgs @("-C", $repoPath, "fetch", "origin", $baseBranch)
        Invoke-Git -GitArgs @("-C", $repoPath, "switch", "-c", $workBranch, "origin/$baseBranch")
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $workspace "AGENTS.md"))) {
    throw "Expected project instructions AGENTS.md are absent; do not launch yet."
}
Write-Host "[OK] MSW repository paired locally with source branch $baseBranch." -ForegroundColor Green
Write-Host "[OK] Local working branch: $workBranch. No changes pushed to GitHub or Render." -ForegroundColor Green
Write-Host "Project folder: $workspace"
if ($PrepareOnly) { return }

Write-Host "Opening interactive Qwen in Codex. Medium reasoning; native node_repl MCP disabled only for this session."
Write-Host "Approvals are ON; workspace sandbox is enabled. No background inference or uncontrolled downloads."
Write-Host "Suggested first task: Read AGENTS.md, README.md and existing tests. Audit one small improvement; do not deploy."
Push-Location $workspace
try {
    & codex --profile aliyun --sandbox workspace-write --ask-for-approval on-request -c 'model_reasoning_effort="medium"' -c 'mcp_servers.node_repl.enabled=false'
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Codex exited with a nonzero status. Review the CLI output without sharing your API key."
    }
}
finally {
    Pop-Location
}
