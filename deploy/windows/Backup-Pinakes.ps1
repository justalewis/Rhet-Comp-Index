#Requires -Version 5.1
<#
.SYNOPSIS
    Takes a consistent local backup of the Pinakes SQLite database.

.DESCRIPTION
    Safe to run while the service is serving traffic: it uses SQLite's online
    backup API (deploy/windows/sqlite_backup.py) rather than a file copy, and
    verifies the result with PRAGMA quick_check before reporting success.

    Do NOT substitute Copy-Item or robocopy for this. Copying a live SQLite
    file can capture a torn page mid-transaction; the copy looks fine right up
    until the day you need to restore it.

    Backups land in <root>\backups as articles-<timestamp>-<label>.db, and the
    oldest are pruned beyond PINAKES_BACKUP_KEEP (default 10).

    This is a LOCAL backup, on the same disk as the database. It protects
    against a bad migration or a bad deploy, not against losing the server.
    For offsite copies, either point Windows Server Backup at the backups
    folder, or configure the six PINAKES_BACKUP_* secrets and use
    Invoke-PinakesAdmin.ps1 -Endpoint Backup, which encrypts and uploads to
    S3/Backblaze.

.PARAMETER Label
    Short suffix for the filename, so you can tell backups apart later.
    Defaults to "scheduled".

.EXAMPLE
    .\Backup-Pinakes.ps1 -Label before-schema-change
#>

[CmdletBinding()]
param(
    [string]$Label = 'scheduled'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

$logFile = Join-Path $PinakesLogDir 'admin-tasks.log'

try {
    if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }

    $path = Backup-PinakesDatabase -Label $Label

    if ($null -eq $path) {
        $line = "{0}  WARN    backup      no database at {1}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $PinakesDbPath
        Add-Content -Path $logFile -Value $line -Encoding utf8
        exit 0
    }

    $sizeMb = [Math]::Round((Get-Item $path).Length / 1MB, 1)
    $line = "{0}  OK      backup      {1} ({2} MB)" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $path, $sizeMb
    Add-Content -Path $logFile -Value $line -Encoding utf8
    exit 0

} catch {
    Write-PinakesLog $_.Exception.Message 'Error'
    try {
        $line = "{0}  ERROR   backup      {1}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $_.Exception.Message
        Add-Content -Path $logFile -Value $line -Encoding utf8
    } catch { }
    exit 1
}
