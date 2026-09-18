#Requires -Version 5.1
<#
    Pinakes.Common.ps1 - shared configuration and helpers for the Pinakes
    Windows Server deployment scripts.

    Dot-source it; do not run it directly:

        . "$PSScriptRoot\Pinakes.Common.ps1"

    Every path and name below can be overridden with a Machine-scope
    environment variable, so a site that installed somewhere other than
    C:\Pinakes never has to edit this file. Set an override like this:

        [Environment]::SetEnvironmentVariable("PINAKES_ROOT","D:\Apps\Pinakes","Machine")

    Targets Windows PowerShell 5.1 (what Server 2019/2022 ship with). No
    PowerShell 7 syntax is used: no &&, no ternary, no null-coalescing.
#>

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 still negotiates TLS 1.0 by default on some builds.
# GitHub and the site itself refuse it, so raise the floor before any request.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12


function Get-PinakesSetting {
    <#  Machine env var, else process env var, else the supplied default. #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name, 'Machine')
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    }
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}


# -- Configuration -----------------------------------------------------------

$PinakesRoot      = Get-PinakesSetting 'PINAKES_ROOT'         'C:\Pinakes'
$PinakesAppDir    = Join-Path $PinakesRoot 'app'
$PinakesVenvDir   = Join-Path $PinakesRoot 'venv'
$PinakesLogDir    = Join-Path $PinakesRoot 'logs'
$PinakesBackupDir = Get-PinakesSetting 'PINAKES_BACKUP_DIR'   (Join-Path $PinakesRoot 'backups')
$PinakesPython    = Join-Path $PinakesVenvDir 'Scripts\python.exe'
$PinakesDbPath    = Get-PinakesSetting 'DB_PATH'              (Join-Path $PinakesRoot 'data\articles.db')
$PinakesPort      = Get-PinakesSetting 'PORT'                 '8080'
$PinakesService   = Get-PinakesSetting 'PINAKES_SERVICE_NAME' 'Pinakes-Web'
$PinakesGitBranch = Get-PinakesSetting 'PINAKES_GIT_BRANCH'   'main'
$PinakesGitRemote = Get-PinakesSetting 'PINAKES_GIT_REMOTE'   'origin'

# Health checks always go to the loopback address, never through IIS. Going
# direct means the check cannot be answered out of the ARR proxy cache, and it
# separates "is the app up?" from "is the reverse proxy configured?".
$PinakesLocalUrl  = "http://127.0.0.1:$PinakesPort"
$PinakesPublicUrl = Get-PinakesSetting 'PINAKES_SITE_URL' 'https://testpinakes.wacclearinghouse.org'

# How many database backups to keep before the oldest are pruned.
$PinakesBackupKeep = [int](Get-PinakesSetting 'PINAKES_BACKUP_KEEP' '10')


# -- Logging -----------------------------------------------------------------

function Write-PinakesLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('Info', 'Step', 'Warn', 'Error', 'Success')][string]$Level = 'Info'
    )
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    switch ($Level) {
        'Step'    { Write-Host ""; Write-Host "[$stamp] == $Message" -ForegroundColor Cyan }
        'Warn'    { Write-Host "[$stamp] WARN  $Message" -ForegroundColor Yellow }
        'Error'   { Write-Host "[$stamp] ERROR $Message" -ForegroundColor Red }
        'Success' { Write-Host "[$stamp] OK    $Message" -ForegroundColor Green }
        default   { Write-Host "[$stamp]       $Message" }
    }
}


function Assert-PinakesAdmin {
    <#  Refuse to continue unless elevated. Stopping and starting a Windows
        service and writing under C:\ both require it. #>
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script must run in an elevated PowerShell window (Right-click Start, then Terminal (Admin))."
    }
}


function Assert-PinakesPrereqs {
    <#  Fail early and specifically, rather than halfway through an update. #>
    if (-not (Test-Path $PinakesAppDir)) {
        throw "Application directory not found: $PinakesAppDir  (override with the PINAKES_ROOT machine variable)"
    }
    if (-not (Test-Path (Join-Path $PinakesAppDir '.git'))) {
        throw "$PinakesAppDir is not a git clone. The update script needs one - see docs/WINDOWS-SERVER-INSTALL.md section 4."
    }
    if (-not (Test-Path $PinakesPython)) {
        throw "Virtual-environment Python not found: $PinakesPython  (see docs/WINDOWS-SERVER-INSTALL.md section 5)"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is not on PATH for this account. Install Git for Windows, or reopen PowerShell after installing it."
    }
    if (-not (Get-Service -Name $PinakesService -ErrorAction SilentlyContinue)) {
        throw "Windows service '$PinakesService' does not exist (see docs/WINDOWS-SERVER-INSTALL.md section 8.2)."
    }
    foreach ($dir in @($PinakesLogDir, $PinakesBackupDir)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
}


# -- Git ---------------------------------------------------------------------

function Invoke-PinakesGit {
    <#  Run git inside the app directory. Returns an object with ExitCode and
        Output. Throws on a non-zero exit unless -AllowFailure.

        Native stderr is merged with 2>&1 and each record forced back to a
        string: in PowerShell 5.1 a native command's stderr line arrives as an
        ErrorRecord, which would otherwise trip $ErrorActionPreference='Stop'
        on git's entirely ordinary progress chatter. #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    Push-Location $PinakesAppDir
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw  = & git @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        Pop-Location
    }
    $text = (@($raw) | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed (exit $code):$([Environment]::NewLine)$text"
    }
    return [PSCustomObject]@{ ExitCode = $code; Output = $text }
}


function Get-PinakesCommit {
    <#  Full SHA of a ref, for example 'HEAD' or 'origin/main'. #>
    param([Parameter(Mandatory = $true)][string]$Ref)
    return (Invoke-PinakesGit -Arguments @('rev-parse', $Ref)).Output.Trim()
}


function Get-PinakesShortCommit {
    param([Parameter(Mandatory = $true)][string]$Ref)
    return (Invoke-PinakesGit -Arguments @('rev-parse', '--short', $Ref)).Output.Trim()
}


function Test-PinakesWorkingTreeClean {
    return [string]::IsNullOrWhiteSpace((Invoke-PinakesGit -Arguments @('status', '--porcelain')).Output)
}


# -- Service control ---------------------------------------------------------

function Stop-PinakesService {
    param([int]$TimeoutSec = 60)
    $svc = Get-Service -Name $PinakesService
    if ($svc.Status -eq 'Stopped') {
        Write-PinakesLog "Service '$PinakesService' is already stopped."
        return
    }
    Write-PinakesLog "Stopping service '$PinakesService' ..."
    Stop-Service -Name $PinakesService -Force
    $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds($TimeoutSec))
    Write-PinakesLog "Service stopped." 'Success'
}


function Start-PinakesService {
    param([int]$TimeoutSec = 60)
    Write-PinakesLog "Starting service '$PinakesService' ..."
    Start-Service -Name $PinakesService
    $svc = Get-Service -Name $PinakesService
    $svc.WaitForStatus('Running', [TimeSpan]::FromSeconds($TimeoutSec))
    Write-PinakesLog "Service reports Running." 'Success'
}


# -- Health ------------------------------------------------------------------

function Get-PinakesHealth {
    <#  Returns the parsed /health payload, or $null if the app did not answer.
        Never throws - callers poll with this. #>
    param([int]$TimeoutSec = 10)
    try {
        return Invoke-RestMethod -Uri "$PinakesLocalUrl/health" -TimeoutSec $TimeoutSec -UseBasicParsing
    } catch {
        return $null
    }
}


function Wait-PinakesHealthy {
    <#  Poll /health until the app answers "ok". When -ExpectedVersion is given,
        also require that /health reports it - that is what proves the service
        actually restarted onto the new code rather than surviving the update.

        health.py computes the version with `git rev-parse --short HEAD` at
        import time, so it only changes on a process restart.

        Returns the health payload on success, $null on timeout. #>
    param(
        [string]$ExpectedVersion,
        [int]$TimeoutSec = 120
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $health = Get-PinakesHealth
        if ($null -ne $health -and $health.status -eq 'ok') {
            if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { return $health }
            if ($health.version -eq $ExpectedVersion) { return $health }
            $last = "app is up but reports version '$($health.version)'; waiting for '$ExpectedVersion'"
        } else {
            $last = "no answer from $PinakesLocalUrl/health yet"
        }
        Start-Sleep -Seconds 3
    }
    if ($last) { Write-PinakesLog $last 'Warn' }
    return $null
}


# -- Database backup ---------------------------------------------------------

function Backup-PinakesDatabase {
    <#  Hot backup through SQLite's online backup API (sqlite_backup.py). Safe
        to run while the service is writing - unlike Copy-Item, which can
        capture a torn file mid-transaction.

        Returns the path of the backup file, or $null if there was no database
        to copy yet. #>
    param([string]$Label = 'manual')

    if (-not (Test-Path $PinakesDbPath)) {
        Write-PinakesLog "No database at $PinakesDbPath yet - nothing to back up." 'Warn'
        return $null
    }
    if (-not (Test-Path $PinakesBackupDir)) {
        New-Item -ItemType Directory -Path $PinakesBackupDir -Force | Out-Null
    }

    $stamp  = (Get-Date).ToString('yyyyMMdd-HHmmss')
    $safe   = ($Label -replace '[^A-Za-z0-9._-]', '-')
    $target = Join-Path $PinakesBackupDir "articles-$stamp-$safe.db"
    $helper = Join-Path $PSScriptRoot 'sqlite_backup.py'

    Write-PinakesLog "Backing up database to $target ..."
    # Capture the helper's stdout rather than letting it fall through. A
    # PowerShell function returns everything it does not consume, so bare
    # output here was returned alongside $target: callers that use the return
    # value got an array whose first element is sqlite_backup.py's
    # "Backing up C:\... -> ..." line, and Get-Item on that fails with
    # "A drive with the name 'Backing up C' does not exist".
    $helperOutput = & $PinakesPython $helper $PinakesDbPath $target
    if ($LASTEXITCODE -ne 0) { throw "Database backup failed (exit $LASTEXITCODE). Refusing to continue." }
    foreach ($line in $helperOutput) { Write-PinakesLog "  $line" }

    $sizeMb = [Math]::Round((Get-Item $target).Length / 1MB, 1)
    Write-PinakesLog "Backup complete ($sizeMb MB)." 'Success'

    # Prune the oldest beyond the retention count.
    $all = @(Get-ChildItem -Path $PinakesBackupDir -Filter 'articles-*.db' | Sort-Object LastWriteTime -Descending)
    if ($all.Count -gt $PinakesBackupKeep) {
        foreach ($f in $all[$PinakesBackupKeep..($all.Count - 1)]) {
            Write-PinakesLog "Pruning old backup $($f.Name)"
            Remove-Item $f.FullName -Force
        }
    }
    return $target
}


# -- Admin token -------------------------------------------------------------

function Get-PinakesAdminToken {
    <#  The bearer token the mutating endpoints require (auth.py). Read from the
        machine environment so it never has to appear in a script or in a
        scheduled task's command line. #>
    $token = Get-PinakesSetting 'PINAKES_ADMIN_TOKEN' ''
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "PINAKES_ADMIN_TOKEN is not set at Machine scope. Set it with: [Environment]::SetEnvironmentVariable('PINAKES_ADMIN_TOKEN','<token>','Machine')"
    }
    return $token
}
