#Requires -Version 5.1
<#
.SYNOPSIS
    Calls one of the Pinakes admin endpoints with the bearer token.

.DESCRIPTION
    The mutating endpoints (auth.py) require an Authorization: Bearer header
    carrying PINAKES_ADMIN_TOKEN. This script reads that token from the machine
    environment, so it never has to appear on a command line, in a scheduled
    task definition, or in this file.

    Used two ways:
      - by hand, when you want to trigger a fetch or a maintenance run;
      - by the Windows scheduled tasks that Install-PinakesTasks.ps1 registers.

    This replaces the old Pinakes-Scheduler service. That service was defined
    to run scheduler.py, which was deleted from the repository in commit
    fa25cae ("Replace standalone scheduler with cron-driven admin endpoints").
    On Fly those jobs are driven by GitHub Actions hitting pinakes.xyz; this
    server needs its own trigger, which is what these tasks are.

    Requests go to the loopback address by default, not through IIS. That
    sidesteps both the reverse proxy and its response cache, and it means the
    jobs keep running even if the public hostname or certificate has a problem.

.PARAMETER Endpoint
    Which job to trigger:
      Fetch       POST /fetch                       incremental article fetch
      DeepFetch   POST /fetch {"deep": true}        full corpus revalidation (slow)
      Prewarm     POST /api/admin/prewarm           rebuild cached aggregates
      Maintenance POST /api/admin/run-maintenance   weekly maintenance
      Digests     POST /api/admin/send-digests      saved-search email digests
      Backup      POST /api/admin/run-backup        offsite S3/B2 backup

    Note on Backup: that endpoint needs all six PINAKES_BACKUP_* secrets and
    uploads to S3/Backblaze. On this server the nightly local backup task uses
    Backup-PinakesDatabase instead, which writes to <root>\backups and needs no
    credentials. Only use Backup here if the offsite secrets are configured.

.PARAMETER BaseUrl
    Override the target. Defaults to http://127.0.0.1:<PORT>.

.PARAMETER TimeoutSec
    Request timeout. Default 120. These endpoints all hand off to a background
    thread and answer immediately, so this only needs to cover the handoff.

.EXAMPLE
    .\Invoke-PinakesAdmin.ps1 -Endpoint Fetch

.EXAMPLE
    .\Invoke-PinakesAdmin.ps1 -Endpoint DeepFetch
    Full revalidation. Takes a long time; run it deliberately, not on a timer.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Fetch', 'DeepFetch', 'Prewarm', 'Maintenance', 'Digests', 'Backup')]
    [string]$Endpoint,

    [string]$BaseUrl,
    [int]$TimeoutSec = 120
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = $PinakesLocalUrl }

$routes = @{
    'Fetch'       = @{ Path = '/fetch';                     Body = $null }
    'DeepFetch'   = @{ Path = '/fetch';                     Body = '{"deep": true}' }
    'Prewarm'     = @{ Path = '/api/admin/prewarm';         Body = $null }
    'Maintenance' = @{ Path = '/api/admin/run-maintenance'; Body = $null }
    'Digests'     = @{ Path = '/api/admin/send-digests';    Body = $null }
    'Backup'      = @{ Path = '/api/admin/run-backup';      Body = $null }
}

$route   = $routes[$Endpoint]
$uri     = "$BaseUrl$($route.Path)"
$logFile = Join-Path $PinakesLogDir 'admin-tasks.log'


function Write-TaskLog {
    <#  Console for interactive use, plus an append-only file so the scheduled
        runs leave a trail. Task Scheduler keeps exit codes but not output. #>
    param([string]$Message, [string]$Level = 'Info')
    Write-PinakesLog $Message $Level
    try {
        if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }
        $line = "{0}  {1,-7} {2,-11} {3}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Level.ToUpper(), $Endpoint, $Message
        Add-Content -Path $logFile -Value $line -Encoding utf8
    } catch { }
}


try {
    $token = Get-PinakesAdminToken

    $headers = @{
        'Authorization' = "Bearer $token"
        'Content-Type'  = 'application/json'
    }

    Write-TaskLog "POST $uri"

    $params = @{
        Uri             = $uri
        Method          = 'POST'
        Headers         = $headers
        TimeoutSec      = $TimeoutSec
        UseBasicParsing = $true
    }
    if ($null -ne $route.Body) { $params['Body'] = $route.Body }

    $response = Invoke-RestMethod @params

    $summary = ($response | ConvertTo-Json -Compress -Depth 4)
    Write-TaskLog "Accepted: $summary" 'Success'

    # These endpoints hand work to a background thread and answer immediately,
    # so a 200 means "accepted", not "finished". Say so, rather than letting a
    # green scheduled task imply the fetch actually succeeded.
    if ($Endpoint -in @('Fetch', 'DeepFetch', 'Prewarm', 'Maintenance', 'Digests')) {
        Write-TaskLog "Work runs in the background. Check $($PinakesLogDir)\web-stdout.log for progress and completion."
    }
    exit 0

} catch {
    # Capture the error record up front. Inside a switch scriptblock $_ is the
    # switch's current item - here $status - not the ErrorRecord, so reaching
    # for $_.Exception.Message down in `default` silently expanded to nothing
    # and every unnamed failure logged "Request failed:" with no reason.
    $err = $_

    $status = $null
    if ($err.Exception.Response) { $status = [int]$err.Exception.Response.StatusCode }

    # 409 from /fetch means a fetch is already running (app._fetch_lock). That
    # is the intended guard against two writers on one SQLite file, not a
    # failure - do not let Task Scheduler light up red for it.
    if ($status -eq 409) {
        Write-TaskLog "A fetch is already in progress; this run was skipped. Not an error." 'Warn'
        exit 0
    }

    switch ($status) {
        401     { Write-TaskLog "401: the Authorization header was missing or malformed." 'Error' }
        403     { Write-TaskLog "403: PINAKES_ADMIN_TOKEN on this machine does not match the one the service is running with. If you changed it, restart '$PinakesService' so the app picks up the new value." 'Error' }
        503     { Write-TaskLog "503: the service reports admin auth is not configured. PINAKES_ADMIN_TOKEN is not set in the service's environment." 'Error' }
        default { Write-TaskLog "Request failed: $($err.Exception.Message)" 'Error' }
    }
    exit 1
}
