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
    throw "Git for Windows is missing. Install it from https://git-scm.com/download/win, open a new PowerShell, then rerun this script."
}

$configPath = Join-Path $HOME ".codex\config.toml"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Missing $configPath. Configure the existing aliyun profile first."
}
$config = Get-Content -LiteralPath $configPath -Raw
if ($config -notmatch '(?m)^\s*\[profiles\.aliyun\]\s*$' -or
    $config -notmatch '(?m)^\s*model\s*=\s*"qwen3\.8-max"\s*$' -or
    $config -notmatch 'token-plan\.ap-southeast-1\.maas\.aliyuncs\.com') {
    throw "Codex profile aliyun is missing Qwen 3.8 Max or the Singapore Token Plan endpoint."
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
    Invoke-Git -GitArgs @("clone", "--single-branch", "--branch", $baseBranch, $repoUrl, $repoPath)
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

Write-Host "Opening interactive Qwen in Codex. Approvals are ON; workspace sandbox is enabled."
Write-Host "Suggested first task: Read AGENTS.md, README.md and existing tests. Audit one small improvement; do not deploy."
Push-Location $workspace
try {
    & codex --profile aliyun --sandbox workspace-write --ask-for-approval on-request
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Codex exited with a nonzero status. Review the CLI output without sharing your API key."
    }
}
finally {
    Pop-Location
}
