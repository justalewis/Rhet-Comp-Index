#Requires -Version 5.1
<#
.SYNOPSIS
    Restarts the Pinakes service when it is running but no longer answering.

.DESCRIPTION
    NSSM restarts the app when the process EXITS. It does nothing when the
    process is alive but stuck, and that is exactly what took the site down on
    2026-10-17: waitress sat listening on 8080 for most of a day with every
    worker thread blocked, IIS returned 502 to every visitor, and the service
    still reported Running. Start-Service was a no-op because, as far as
    Windows knew, nothing was wrong.

    This script closes that gap. Pinakes-HealthWatchdog runs it every five
    minutes. Each run:

      1. Does nothing if the service is Stopped (someone stopped it on
         purpose), if an Update-Pinakes.ps1 deploy is in progress, if the
         pause file exists, or if the process started less than -GraceMinutes
         ago (schema migrations run at startup).
      2. Asks http://127.0.0.1:<port>/health up to -Attempts times.
         /health does no database work and normally answers in under a
         millisecond, so a timeout means every waitress thread is busy or
         stuck - not that the database is slow.
      3. If every attempt fails: saves a thread dump with py-spy (when it is
         installed in the venv) so the cause can be found afterwards, restarts
         the service, killing the python processes if the stop hangs, and
         waits for /health to come back.

    Restarts are written to logs\watchdog.log, to admin-tasks.log, and to the
    Windows Application event log (source "Pinakes", event 1001 = restarted,
    1002 = restart did not bring it back), so the server's own monitoring can
    pick them up.

    A restart kills whatever background job was running inside the app (a
    fetch, prewarm, maintenance). All of them are safe to re-run; a hung site
    is the worse outcome.

    Also prunes NSSM's rotated web-stdout/web-stderr logs and old thread dumps.

    To stop the watchdog acting during planned work, create the pause file:

        New-Item C:\Pinakes\watchdog.pause -ItemType File

    and delete it afterwards. It does not need the task to be disabled.

.PARAMETER TimeoutSec
    How long each /health request may take. Default 30.

.PARAMETER Attempts
    Consecutive failed requests before a restart. Default 3.

.PARAMETER RetryDelaySec
    Pause between attempts. Default 20, so a restart needs about 2.5 minutes
    of solid failure.

.PARAMETER GraceMinutes
    Leave a freshly started process alone for this long. Default 5.

.PARAMETER DryRun
    Report what would happen; never restart.

.EXAMPLE
    .\Watch-PinakesHealth.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [int]$TimeoutSec = 30,
    [int]$Attempts = 3,
    [int]$RetryDelaySec = 20,
    [int]$GraceMinutes = 5,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

$watchLog  = Join-Path $PinakesLogDir 'watchdog.log'
$taskLog   = Join-Path $PinakesLogDir 'admin-tasks.log'
$pauseFile = Join-Path $PinakesRoot 'watchdog.pause'
$pySpy     = Join-Path $PinakesVenvDir 'Scripts\py-spy.exe'
$logKeep   = [int](Get-PinakesSetting 'PINAKES_LOG_KEEP' '14')


function Write-WatchLog {
    param([string]$Status, [string]$Message, [switch]$Job)
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Add-Content -Path $watchLog -Value ("{0}  {1,-7} {2}" -f $stamp, $Status, $Message) -Encoding utf8
    if ($Job) {
        Add-Content -Path $taskLog -Value ("{0}  {1,-7} Watchdog    {2}" -f $stamp, $Status, $Message) -Encoding utf8
    }
    Write-Host "[$stamp] $Status $Message"
}


function Write-WatchEvent {
    <#  Best effort: the event log is for the server's monitoring, never a
        reason to fail the restart. #>
    param([int]$EventId, [string]$Message, [string]$EntryType = 'Warning')
    try {
        if (-not [Diagnostics.EventLog]::SourceExists('Pinakes')) {
            New-EventLog -LogName Application -Source 'Pinakes'
        }
        Write-EventLog -LogName Application -Source 'Pinakes' -EventId $EventId -EntryType $EntryType -Message $Message
    } catch {
        Write-WatchLog 'WARN' "Could not write to the event log: $($_.Exception.Message)"
    }
}


function Remove-OldFiles {
    param([string]$Filter, [int]$Keep)
    $files = @(Get-ChildItem -Path $PinakesLogDir -Filter $Filter -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending)
    if ($files.Count -gt $Keep) {
        foreach ($f in $files[$Keep..($files.Count - 1)]) {
            Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}


function Save-ThreadDump {
    <#  What every thread in the stuck process was doing. Without this a hang
        leaves no evidence: the restart wipes it. #>
    param([int]$ProcessId)
    if (-not (Test-Path $pySpy)) {
        Write-WatchLog 'INFO' "No thread dump: py-spy is not installed (C:\Pinakes\venv\Scripts\pip.exe install py-spy)."
        return
    }
    $dump = Join-Path $PinakesLogDir ("hang-{0}.txt" -f (Get-Date).ToString('yyyyMMdd-HHmmss'))
    try {
        $p = Start-Process -FilePath $pySpy -ArgumentList @('dump', '--pid', $ProcessId) `
            -RedirectStandardOutput $dump -RedirectStandardError "$dump.err" -NoNewWindow -PassThru
        if (-not $p.WaitForExit(60000)) {
            $p.Kill()
            Write-WatchLog 'WARN' "py-spy did not finish in 60s; partial dump at $dump"
        } else {
            Write-WatchLog 'INFO' "Thread dump saved: $dump"
        }
        if ((Test-Path "$dump.err") -and (Get-Item "$dump.err").Length -eq 0) { Remove-Item "$dump.err" -Force }
    } catch {
        Write-WatchLog 'WARN' "Thread dump failed: $($_.Exception.Message)"
    }
}


function Restart-HungService {
    param($Processes)
    $svc = Get-Service -Name $PinakesService
    try {
        Stop-Service -Name $PinakesService -Force -ErrorAction Stop
        $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
    } catch {
        # A hung interpreter can ignore NSSM's console Ctrl+C. Kill it outright.
        Write-WatchLog 'WARN' "Service did not stop cleanly ($($_.Exception.Message)); killing the python processes."
        foreach ($p in $Processes) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
    }
    Start-Service -Name $PinakesService
    $svc.WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
}


try {
    if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }

    Remove-OldFiles -Filter 'web-stdout-*.log' -Keep $logKeep
    Remove-OldFiles -Filter 'web-stderr-*.log' -Keep $logKeep
    Remove-OldFiles -Filter 'hang-*.txt' -Keep 20
    Remove-OldFiles -Filter 'update-*.log' -Keep 30

    if (Test-Path $pauseFile) {
        Write-Host "Paused: $pauseFile exists. Nothing checked."
        exit 0
    }

    $svc = Get-Service -Name $PinakesService -ErrorAction SilentlyContinue
    if ($null -eq $svc) { throw "Windows service '$PinakesService' does not exist (set PINAKES_SERVICE_NAME)." }
    if ($svc.Status -ne 'Running') {
        # Stopped on purpose, or mid-start/stop. Not the watchdog's call.
        Write-Host "Service is $($svc.Status); leaving it alone."
        exit 0
    }

    $deploying = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
                   Where-Object { $_.CommandLine -like '*Update-Pinakes.ps1*' -and $_.CommandLine -notlike '*-Check*' })
    if ($deploying.Count -gt 0) {
        Write-Host "A deploy (Update-Pinakes.ps1) is running; leaving the service alone."
        exit 0
    }

    $procs = @(Get-PinakesServiceProcesses)
    if ($procs.Count -gt 0) {
        $started = $procs[-1].CreationDate
        if ($started -and ((Get-Date) - $started).TotalMinutes -lt $GraceMinutes) {
            Write-Host "Process started at $started; inside the ${GraceMinutes}-minute startup grace."
            exit 0
        }
    }

    for ($i = 1; $i -le $Attempts; $i++) {
        $health = Get-PinakesHealth -TimeoutSec $TimeoutSec
        if ($null -ne $health -and $health.status -eq 'ok') {
            if ($i -gt 1) { Write-WatchLog 'OK' "/health answered on attempt $i of $Attempts; no restart." }
            exit 0
        }
        if ($i -lt $Attempts) { Start-Sleep -Seconds $RetryDelaySec }
    }

    $pids = ($procs | ForEach-Object { $_.ProcessId }) -join ', '
    $why = "/health on $PinakesLocalUrl did not answer within ${TimeoutSec}s, $Attempts times in a row (service Running, python PIDs: $pids)."

    if ($DryRun) {
        Write-WatchLog 'DRYRUN' "$why Would restart '$PinakesService'."
        exit 0
    }

    Write-WatchLog 'FAIL' "$why Restarting '$PinakesService'." -Job
    if ($procs.Count -gt 0) { Save-ThreadDump -ProcessId $procs[-1].ProcessId }

    Restart-HungService -Processes $procs

    $back = Wait-PinakesHealthy -TimeoutSec 180
    if ($null -ne $back) {
        $msg = "Restarted '$PinakesService' after it stopped answering. /health is ok again (version $($back.version))."
        Write-WatchLog 'OK' $msg -Job
        Write-WatchEvent -EventId 1001 -Message "$why`r`n`r`n$msg`r`nThread dump (if any): $PinakesLogDir\hang-*.txt"
        exit 0
    }

    $msg = "Restarted '$PinakesService' but /health still does not answer after 180s. The site is DOWN. See $PinakesLogDir\web-stderr.log."
    Write-WatchLog 'ERROR' $msg -Job
    Write-WatchEvent -EventId 1002 -EntryType 'Error' -Message "$why`r`n`r`n$msg"
    exit 1

} catch {
    Write-WatchLog 'ERROR' $_.Exception.Message -Job
    exit 1
}
