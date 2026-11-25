#Requires -Version 5.1
<#
.SYNOPSIS
    One-time load of "Writing Spaces: Readings on Writing" (vols 1-6 + the Assignment and Activity Archive) into the
    live database.

.DESCRIPTION
    Run this AFTER Update-Pinakes.ps1 has deployed the commit that registers the series (journals.py: no-issn:wsp-b).

    It does not replace the database. It adds this one title's rows to the live database:
      1. takes a verified online backup (Backup-Pinakes.ps1 -Label before-writing-spaces),
      2. runs ingest_writing_spaces.py with the venv Python: 221 items and 1,619 references from the committed
         data\writing_spaces.json (built from the Crossref deposit; no network calls, takes seconds),
      3. restarts the service so the sidebar, coverage and tag caches pick up the new title.

    Avoid the 03:23 daily fetch and the Sunday 04:45 maintenance window: both write to the same database.
    The log is <root>\logs\writing-spaces-load.log. Safe to re-run: items already indexed are skipped and each item's
    reference list is replaced, not appended.

.EXAMPLE
    .\Load-WritingSpaces.ps1
#>

[CmdletBinding()]
param(
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

if (-not (Test-Path $PinakesPython)) { throw "venv Python not found: $PinakesPython" }
if (-not (Test-Path $PinakesDbPath)) { throw "Database not found: $PinakesDbPath" }
$loader = Join-Path $PinakesAppDir 'ingest_writing_spaces.py'
$data   = Join-Path $PinakesAppDir 'data\writing_spaces.json'
if (-not (Test-Path $loader) -or -not (Test-Path $data)) { throw "Loader or data file missing. Run Update-Pinakes.ps1 first." }
if (-not (Select-String -Path (Join-Path $PinakesAppDir 'journals.py') -Pattern 'no-issn:wsp-b' -Quiet)) {
    throw "journals.py on this server does not register Writing Spaces yet. Run Update-Pinakes.ps1 first."
}
if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }

Write-PinakesLog "Backing up the database before the Writing Spaces load..."
& "$PSScriptRoot\Backup-Pinakes.ps1" -Label before-writing-spaces
if ($LASTEXITCODE -ne 0) { throw "Backup failed; not loading anything." }

$log = Join-Path $PinakesLogDir 'writing-spaces-load.log'
$env:DB_PATH    = $PinakesDbPath
$env:PYTHONPATH = $PinakesAppDir
$env:PYTHONIOENCODING = 'utf-8'
Write-PinakesLog "Loading Writing Spaces (log: $log)."
Push-Location $PinakesAppDir
# Windows PowerShell 5.1 wraps every line a native program writes to stderr in an ErrorRecord, and Python's logging
# writes to stderr. With $ErrorActionPreference = 'Stop' the first such line would abort the script, so relax it for
# this one call, flatten the records back to text, and judge success by the exit code instead (as Load-WLN.ps1 does).
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
    Restart-Service $PinakesService -Force
    Start-Sleep -Seconds 8
}
Write-PinakesLog "Done. Check: $PinakesPublicUrl/?journal=Writing%20Spaces%3A%20Readings%20on%20Writing and run Test-PinakesHealth.ps1"
