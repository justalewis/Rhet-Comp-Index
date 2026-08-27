#Requires -Version 5.1
<#
.SYNOPSIS
    Updates the Pinakes installation on this server to the latest commit on
    GitHub, verifying the result and rolling back automatically if it fails.

.DESCRIPTION
    Run this whenever there is a new release to deploy. It is safe to run when
    there is nothing to do - it checks first and exits early.

    What it does, in order:

      1. Verifies prerequisites (elevated shell, git, venv, service present).
      2. Fetches from GitHub and compares the deployed commit to the target.
         If they match, stops here.
      3. Takes a hot backup of the SQLite database (safe while running).
      4. Stops the Pinakes-Web service.
      5. Moves the working tree to the target commit.
      6. Reinstalls Python dependencies.
      7. Smoke-tests that the new code imports against a scratch database.
      8. Starts the service and polls /health until it reports the NEW commit.
      9. If any of 5-8 fails, restores the previous commit, reinstalls its
         dependencies, restarts, and reports what happened.

    The version check in step 8 is the point of the whole script. The app
    computes its version with `git rev-parse --short HEAD` when the process
    starts (health.py), so /health reporting the new SHA is proof that the
    service really restarted onto the new code - not that it merely survived.

    A full transcript is written to <root>\logs\update-<timestamp>.log.

.PARAMETER Check
    Report what would change and exit without touching anything. Run this
    first if you want to see the pending commits before committing to a
    deploy. Does not need an elevated shell.

.PARAMETER Ref
    Git ref to deploy. Defaults to origin/main. Accepts a tag or a full SHA,
    which is how you deploy a specific release or step back to a known-good
    commit: -Ref e0f82ec

.PARAMETER Force
    Deploy even when the working tree has uncommitted local modifications.
    Those modifications are DISCARDED. Without this, local edits abort the
    update, because silently throwing away someone's hand-patch on a server
    is worse than stopping.

.PARAMETER SkipBackup
    Skip the pre-update database backup. Only sensible when you have just
    taken one by hand, or when the database is empty.

.PARAMETER TimeoutSec
    How long to wait for the app to come up healthy on the new code.
    Default 180. Raise it if the server is slow or the database is very large;
    startup runs schema migrations before it answers.

.EXAMPLE
    .\Update-Pinakes.ps1 -Check
    Show which commits would be deployed. Changes nothing.

.EXAMPLE
    .\Update-Pinakes.ps1
    Deploy origin/main, with backup, verification, and rollback on failure.

.EXAMPLE
    .\Update-Pinakes.ps1 -Ref e0f82ec
    Deliberately step back to an earlier commit.
#>

[CmdletBinding()]
param(
    [switch]$Check,
    [string]$Ref,
    [switch]$Force,
    [switch]$SkipBackup,
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

if ([string]::IsNullOrWhiteSpace($Ref)) { $Ref = "$PinakesGitRemote/$PinakesGitBranch" }

$transcript = $null
if (-not $Check) {
    if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }
    $transcript = Join-Path $PinakesLogDir ("update-" + (Get-Date).ToString('yyyyMMdd-HHmmss') + ".log")
    Start-Transcript -Path $transcript | Out-Null
}


function Restore-PreviousCommit {
    <#  Best-effort rollback. Called only when the new code failed to come up.
        Deliberately does not throw: the caller is already in a failure path
        and needs to finish reporting. #>
    param([Parameter(Mandatory = $true)][string]$Sha)

    Write-PinakesLog "ROLLING BACK to $Sha" 'Warn'
    try {
        Stop-PinakesService
        Invoke-PinakesGit -Arguments @('reset', '--hard', $Sha) | Out-Null
        & $PinakesPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $PinakesAppDir 'requirements.txt')
        Start-PinakesService

        $short = Get-PinakesShortCommit 'HEAD'
        $ok = Wait-PinakesHealthy -ExpectedVersion $short -TimeoutSec $TimeoutSec
        if ($null -ne $ok) {
            Write-PinakesLog "Rollback succeeded. The site is running the previous version ($short)." 'Success'
            return $true
        }
        Write-PinakesLog "Rollback restored the old code but the app is STILL not healthy." 'Error'
        return $false
    } catch {
        Write-PinakesLog "Rollback itself failed: $($_.Exception.Message)" 'Error'
        return $false
    }
}


try {
    Write-PinakesLog "Pinakes update" 'Step'
    Write-PinakesLog "Root:     $PinakesRoot"
    Write-PinakesLog "App:      $PinakesAppDir"
    Write-PinakesLog "Service:  $PinakesService"
    Write-PinakesLog "Database: $PinakesDbPath"
    Write-PinakesLog "Target:   $Ref"

    if (-not $Check) { Assert-PinakesAdmin }
    Assert-PinakesPrereqs

    # -- 1. What is deployed, and what would be? -----------------------------

    Write-PinakesLog "Checking GitHub for changes" 'Step'
    Invoke-PinakesGit -Arguments @('fetch', $PinakesGitRemote, '--prune', '--tags') | Out-Null

    $currentSha   = Get-PinakesCommit 'HEAD'
    $currentShort = Get-PinakesShortCommit 'HEAD'
    $targetSha    = Get-PinakesCommit $Ref
    $targetShort  = Get-PinakesShortCommit $Ref

    Write-PinakesLog "Deployed commit: $currentShort"
    Write-PinakesLog "Target commit:   $targetShort"

    # What the running process actually reports, which can differ from the
    # working tree if someone pulled without restarting the service.
    $live = Get-PinakesHealth
    if ($null -eq $live) {
        Write-PinakesLog "The app is not answering on $PinakesLocalUrl/health right now." 'Warn'
    } else {
        Write-PinakesLog "Running process reports version '$($live.version)', up $([Math]::Round($live.uptime_seconds / 3600, 1))h"
        if ($live.version -ne $currentShort) {
            Write-PinakesLog "Working tree is at $currentShort but the SERVICE is running $($live.version). Someone pulled without restarting. This update will resolve it." 'Warn'
        }
    }

    if ($currentSha -eq $targetSha) {
        if ($null -ne $live -and $live.version -eq $currentShort) {
            Write-PinakesLog "Already up to date at $currentShort. Nothing to do." 'Success'
            exit 0
        }
        Write-PinakesLog "Code is already at $targetShort but the service is not running it. Continuing in order to restart." 'Warn'
    } else {
        $pending = (Invoke-PinakesGit -Arguments @('log', '--oneline', '--no-decorate', "$currentSha..$targetSha")).Output
        if (-not [string]::IsNullOrWhiteSpace($pending)) {
            Write-PinakesLog "Commits to be deployed:"
            $pending -split "`n" | ForEach-Object { if ($_.Trim()) { Write-Host "        $($_.Trim())" } }
        } else {
            Write-PinakesLog "Target is BEHIND the deployed commit - this is a step backwards." 'Warn'
            $reverting = (Invoke-PinakesGit -Arguments @('log', '--oneline', '--no-decorate', "$targetSha..$currentSha")).Output
            $reverting -split "`n" | ForEach-Object { if ($_.Trim()) { Write-Host "        (reverting) $($_.Trim())" } }
        }
    }

    if ($Check) {
        Write-PinakesLog "-Check specified. Nothing was changed." 'Success'
        exit 0
    }

    # -- 2. Refuse to clobber local edits unless told to ---------------------

    if (-not (Test-PinakesWorkingTreeClean)) {
        $dirty = (Invoke-PinakesGit -Arguments @('status', '--short')).Output
        if (-not $Force) {
            Write-PinakesLog "The working tree has local modifications:" 'Error'
            Write-Host $dirty
            throw "Refusing to discard local changes. Review them, then re-run with -Force to discard, or commit them."
        }
        Write-PinakesLog "-Force specified; discarding these local modifications:" 'Warn'
        Write-Host $dirty
    }

    # -- 3. Backup ------------------------------------------------------------

    if ($SkipBackup) {
        Write-PinakesLog "Skipping database backup (-SkipBackup)." 'Warn'
    } else {
        Write-PinakesLog "Backing up the database" 'Step'
        Backup-PinakesDatabase -Label "pre-$targetShort" | Out-Null
    }

    # -- 4-6. Deploy ----------------------------------------------------------

    Write-PinakesLog "Deploying $targetShort" 'Step'
    Stop-PinakesService

    Invoke-PinakesGit -Arguments @('reset', '--hard', $targetSha) | Out-Null
    # Stale .pyc files from deleted modules can shadow a rename and produce
    # baffling ImportErrors. Untracked-but-ignored cruft is not worth keeping.
    Invoke-PinakesGit -Arguments @('clean', '-fd') | Out-Null
    Write-PinakesLog "Working tree now at $(Get-PinakesShortCommit 'HEAD')." 'Success'

    Write-PinakesLog "Installing Python dependencies ..."
    & $PinakesPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $PinakesAppDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-PinakesLog "pip install failed." 'Error'
        Restore-PreviousCommit -Sha $currentSha | Out-Null
        throw "Dependency installation failed; rolled back to $currentShort."
    }
    # waitress replaces gunicorn on Windows and is deliberately not in
    # requirements.txt (which targets the Linux container). Keep it present.
    & $PinakesPython -m pip install --quiet --disable-pip-version-check waitress | Out-Null
    Write-PinakesLog "Dependencies installed." 'Success'

    # Import smoke test against a scratch database, so a syntax error or a
    # missing dependency is caught before the service is asked to start, and
    # without touching real data. This does not exercise migrations against the
    # live schema - the health check below is what covers that.
    Write-PinakesLog "Smoke-testing the new code ..."
    $scratch = Join-Path $env:TEMP ("pinakes-import-check-" + [Guid]::NewGuid().ToString('N') + ".db")
    $previousDb = $env:DB_PATH
    try {
        $env:DB_PATH = $scratch
        Push-Location $PinakesAppDir
        & $PinakesPython -c "import app; print('import ok')"
        $importCode = $LASTEXITCODE
        Pop-Location
    } finally {
        $env:DB_PATH = $previousDb
        Remove-Item $scratch -Force -ErrorAction SilentlyContinue
    }
    if ($importCode -ne 0) {
        Write-PinakesLog "The new code does not import." 'Error'
        Restore-PreviousCommit -Sha $currentSha | Out-Null
        throw "Import smoke test failed; rolled back to $currentShort."
    }
    Write-PinakesLog "Smoke test passed." 'Success'

    # -- 7. Start and verify --------------------------------------------------

    Write-PinakesLog "Starting and verifying" 'Step'
    Start-PinakesService

    Write-PinakesLog "Waiting for /health to report version '$targetShort' (up to ${TimeoutSec}s; schema migrations run first) ..."
    $health = Wait-PinakesHealthy -ExpectedVersion $targetShort -TimeoutSec $TimeoutSec

    if ($null -eq $health) {
        Write-PinakesLog "The app did not come up healthy on $targetShort." 'Error'
        Write-PinakesLog "Last 40 lines of the service error log:" 'Warn'
        $errLog = Join-Path $PinakesLogDir 'web-stderr.log'
        if (Test-Path $errLog) { Get-Content $errLog -Tail 40 } else { Write-Host "        (no log at $errLog)" }

        $rolled = Restore-PreviousCommit -Sha $currentSha
        if ($rolled) {
            throw "Update to $targetShort failed; the site is back on $currentShort. The service error log above shows why."
        }
        throw "Update to $targetShort failed AND rollback failed. The site is DOWN. Restore a database backup from $PinakesBackupDir if needed, and see the service log at $errLog."
    }

    # -- 8. Report ------------------------------------------------------------

    Write-PinakesLog "Update complete" 'Step'
    Write-PinakesLog "Version:    $($health.version)" 'Success'
    Write-PinakesLog "Admin auth: $($health.admin_auth)"

    $ready = $null
    try { $ready = Invoke-RestMethod -Uri "$PinakesLocalUrl/health/ready" -TimeoutSec 15 -UseBasicParsing } catch { }
    if ($null -ne $ready) { Write-PinakesLog "Database:   $($ready.db)" }

    try {
        $public = Invoke-WebRequest -Uri $PinakesPublicUrl -TimeoutSec 20 -UseBasicParsing
        Write-PinakesLog "Public URL: $PinakesPublicUrl returned HTTP $($public.StatusCode)" 'Success'
    } catch {
        Write-PinakesLog "Public URL $PinakesPublicUrl did not respond: $($_.Exception.Message). The app itself is healthy, so look at the IIS/ARR reverse proxy." 'Warn'
    }

    Write-PinakesLog "Transcript: $transcript"
    exit 0

} catch {
    Write-PinakesLog $_.Exception.Message 'Error'
    if ($transcript) { Write-PinakesLog "Full transcript: $transcript" }
    exit 1
} finally {
    if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
}
