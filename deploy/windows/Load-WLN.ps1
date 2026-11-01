#Requires -Version 5.1
<#
.SYNOPSIS
    One-time load of "WLN: A Journal of Writing Center Scholarship" (v1-51, article-level DOIs) into the live database.

.DESCRIPTION
    Run this AFTER Update-Pinakes.ps1 has deployed the commit that registers the journal (journals.py: no-issn:wln-j).

    It does not replace the database. It adds this one journal's rows to the live database, which keeps everything the
    server has gathered since the last copy from the dev machine:
      1. takes a verified online backup (Backup-Pinakes.ps1 -Label before-wln),
      2. runs _prod_wln_load.py with the venv Python (CrossRef fetch -> reference harvest for WLN only -> counts),
      3. restarts the service so the in-process caches pick up the new journal.

    The run takes roughly 30-45 minutes (about 1,800 articles at one request per half second). The log is
    <root>\logs\wln-load.log. Safe to re-run: article inserts are skipped when the DOI exists and each article's
    citations are wiped and re-inserted.

.EXAMPLE
    .\Load-WLN.ps1
    Get-Content C:\Pinakes\logs\wln-load.log -Wait      # watch it
#>

[CmdletBinding()]
param(
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

if (-not (Test-Path $PinakesPython)) { throw "venv Python not found: $PinakesPython" }
if (-not (Test-Path $PinakesDbPath)) { throw "Database not found: $PinakesDbPath" }
$loader = Join-Path $PinakesAppDir '_prod_wln_load.py'
if (-not (Test-Path $loader)) { throw "Loader not found ($loader). Run Update-Pinakes.ps1 first." }
if (-not (Select-String -Path (Join-Path $PinakesAppDir 'journals.py') -Pattern 'no-issn:wln-j' -Quiet)) {
    throw "journals.py on this server does not register WLN yet. Run Update-Pinakes.ps1 first."
}
if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }

Write-PinakesLog "Backing up the database before the WLN load..."
& "$PSScriptRoot\Backup-Pinakes.ps1" -Label before-wln
if ($LASTEXITCODE -ne 0) { throw "Backup failed; not loading anything." }

$log = Join-Path $PinakesLogDir 'wln-load.log'
$env:DB_PATH    = $PinakesDbPath
$env:PYTHONPATH = $PinakesAppDir
$env:PYTHONIOENCODING = 'utf-8'
Write-PinakesLog "Loading WLN (log: $log). This takes a while."
Push-Location $PinakesAppDir
# Windows PowerShell 5.1 wraps every line a native program writes to stderr in an ErrorRecord, and Python's logging writes
# its INFO lines to stderr. With $ErrorActionPreference = 'Stop' the very first log line aborts the script, so relax it
# for this one call, flatten the records back to text, and judge success by the exit code instead.
$prevEap = $ErrorActionPreference
$prevEnc = [Console]::OutputEncoding
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$loaderExit = 0
try {
    & $PinakesPython $loader 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
    $loaderExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
    [Console]::OutputEncoding = $prevEnc
    Pop-Location
}
if ($loaderExit -ne 0) { throw "Loader exited with code $loaderExit (see $log)" }

if (-not $NoRestart) {
    Write-PinakesLog "Restarting $PinakesService so cached sidebar / coverage numbers refresh..."
    Restart-Service $PinakesService
    Start-Sleep -Seconds 8
}
Write-PinakesLog "Done. Check: $PinakesPublicUrl (journal list) and run Test-PinakesHealth.ps1"
