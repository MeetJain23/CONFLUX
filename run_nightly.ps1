# CONFLUX nightly runner (local scheduler path).
#
# Runs the whole pipeline on THIS machine, where the moat CSVs live:
#   1. refresh data feeds locally (prices, news, macros) into local SQLite
#   2. score the full universe locally into local SQLite
#   3. sync finished results to Neon (results only, never the moat)
#
# Logs to logs\nightly_YYYY-MM-DD.log for morning review.
#
# Run manually:  powershell.exe -ExecutionPolicy Bypass -File .\run_nightly.ps1
# Or via Task Scheduler on weekdays after market close.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$today  = Get-Date -Format "yyyy-MM-dd"
$logdir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$log = Join-Path $logdir "nightly_$today.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $log -Value $line
}

Log "=== CONFLUX nightly run start ==="

# Activate venv
$venv = Join-Path $root ".venv\Scripts\Activate.ps1"
if (Test-Path $venv) {
    . $venv
    Log "venv activated"
} else {
    Log "WARNING: venv not found at $venv, using system python"
}

# Step 1: refresh data feeds LOCALLY.
# Blank the Neon URL for this step only so ingestion writes to local SQLite
# (row-by-row writes must not hit Neon, which drops idle connections).
Log "[1/3] refreshing data feeds (local)"
$saved = $env:CONFLUX_DATABASE_URL
$env:CONFLUX_DATABASE_URL = ""
python -m scripts.run_daily $today 2>&1 | Tee-Object -FilePath $log -Append
$env:CONFLUX_DATABASE_URL = $saved
Log "[1/3] data refresh done"

# Step 2: score full universe LOCALLY
Log "[2/3] scoring full universe (local)"
python -m scripts.score_local $today 2>&1 | Tee-Object -FilePath $log -Append
Log "[2/3] scoring done"

# Step 3: sync results to Neon
Log "[3/3] syncing results to Neon"
python -m scripts.sync_to_neon $today 2>&1 | Tee-Object -FilePath $log -Append
Log "[3/3] sync done"

Log "=== CONFLUX nightly run complete ==="