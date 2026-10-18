#Requires -Version 5.1
<#
.SYNOPSIS
    Brings the NSSM service that runs Pinakes to the settings it needs:
    log files, log rotation, and waitress worker threads.

.DESCRIPTION
    The service on the Clearinghouse server was installed with none of these:

      * No AppStdout/AppStderr. Everything waitress and the app printed went
        nowhere, so web-stdout.log and web-stderr.log - which the runbook,
        Invoke-PinakesAdmin.ps1 and Update-Pinakes.ps1 all tell you to read -
        did not exist. When the app hung on 2026-10-17 there was no log to
        read.
      * No --threads, so waitress ran its default of 4 worker threads. Four
        slow requests (a crawler on /citations, a big export) and the site
        stops answering. 8 matches what gunicorn ran on Fly.
      * Python's output buffered, so even with log files the last lines
        before a hang would still be sitting in memory.

    This script sets, on the existing service:

      AppStdout / AppStderr   <root>\logs\web-stdout.log, web-stderr.log
      AppRotateFiles/Online   rotate while running, daily or at 10 MB;
                              Watch-PinakesHealth.ps1 prunes old ones
                              (PINAKES_LOG_KEEP, default 14 of each)
      AppParameters           --listen=<unchanged> --threads=<n>
                              --channel-timeout=<s> app:app
      AppEnvironmentExtra     adds PYTHONUNBUFFERED=1, keeps every existing
                              line (DB_PATH, PINAKES_ADMIN_TOKEN, ...)
      AppExit                 Restart (restart if the process exits)

    The listen address is kept as it is now - IIS/ARR is pointed at it, and
    changing it is an IIS decision, not this script's.

    Then it restarts the service and waits for /health, unless -NoRestart.
    A restart interrupts any background job running in the app; check
    admin-tasks.log first, or run it outside 02:00-06:00.

    Safe to re-run.

.PARAMETER Threads
    Waitress worker threads. Default PINAKES_WAITRESS_THREADS, else 8. Keep
    this ONE process: the rate limiter, the fetch lock and the tag index are
    all in-process and break with more than one.

.PARAMETER ChannelTimeoutSec
    Seconds before waitress drops an idle connection. Default 120.

.PARAMETER Show
    Print the current service settings and exit. Changes nothing.

.PARAMETER NoRestart
    Write the settings but do not restart; they apply at the next restart.

.EXAMPLE
    .\Set-PinakesService.ps1 -Show

.EXAMPLE
    .\Set-PinakesService.ps1
#>

[CmdletBinding()]
param(
    [int]$Threads = 0,
    [int]$ChannelTimeoutSec = 120,
    [switch]$Show,
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

if ($Threads -eq 0) { $Threads = [int](Get-PinakesSetting 'PINAKES_WAITRESS_THREADS' '8') }

$paramsKey = "HKLM:\SYSTEM\CurrentControlSet\Services\$PinakesService\Parameters"
$stdoutLog = Join-Path $PinakesLogDir 'web-stdout.log'
$stderrLog = Join-Path $PinakesLogDir 'web-stderr.log'


function Get-NssmPath {
    <#  nssm.exe is often not on PATH (it is not on the Clearinghouse box);
        the service definition always knows where it is. #>
    $svc = Get-CimInstance Win32_Service -Filter "Name='$PinakesService'"
    if ($null -eq $svc) { throw "Windows service '$PinakesService' does not exist (set PINAKES_SERVICE_NAME)." }
    $exe = $svc.PathName.Trim()
    if ($exe.StartsWith('"')) { $exe = $exe.Substring(1, $exe.IndexOf('"', 1) - 1) }
    if ($exe -notlike '*nssm*.exe') { throw "Service '$PinakesService' is not an NSSM service (PathName: $($svc.PathName))." }
    if (-not (Test-Path $exe)) { throw "nssm.exe not found at $exe" }
    return $exe
}


function Invoke-Nssm {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # nssm writes UTF-16 to the console; strip the NULs PowerShell 5.1 keeps.
        $raw  = & $script:nssm @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $text = ((@($raw) | ForEach-Object { $_.ToString() }) -join ' ') -replace "`0", ''
    if ($code -ne 0) { throw "nssm $($Arguments -join ' ') failed (exit $code): $text" }
}


function Get-ListenArgs {
    <#  Keep whatever address IIS is proxying to. Understands both --listen=
        and the --host=/--port= pair the install guide used. #>
    param([string]$Current)
    $listen = @([regex]::Matches($Current, '--listen[= ](\S+)') | ForEach-Object { "--listen=$($_.Groups[1].Value)" })
    if ($listen.Count -gt 0) { return $listen }
    $h = [regex]::Match($Current, '--host[= ](\S+)')
    $p = [regex]::Match($Current, '--port[= ](\d+)')
    $hostPart = '127.0.0.1'
    $portPart = $PinakesPort
    if ($h.Success) { $hostPart = $h.Groups[1].Value }
    if ($p.Success) { $portPart = $p.Groups[1].Value }
    return @("--listen=${hostPart}:${portPart}")
}


function Show-ServiceSettings {
    $p = Get-ItemProperty -Path $paramsKey
    $exit = (Get-ItemProperty -Path "$paramsKey\AppExit" -ErrorAction SilentlyContinue).'(default)'
    Write-Host ""
    Write-Host "  Service          $PinakesService ($((Get-Service $PinakesService).Status))"
    Write-Host "  nssm.exe         $script:nssm"
    Write-Host "  Application      $($p.Application)"
    Write-Host "  AppParameters    $($p.AppParameters)"
    Write-Host "  AppDirectory     $($p.AppDirectory)"
    Write-Host "  AppStdout        $($p.AppStdout)"
    Write-Host "  AppStderr        $($p.AppStderr)"
    Write-Host "  AppRotateFiles   $($p.AppRotateFiles)  online=$($p.AppRotateOnline)  seconds=$($p.AppRotateSeconds)  bytes=$($p.AppRotateBytes)"
    Write-Host "  AppExit          $exit"
    # Names only: the values include PINAKES_ADMIN_TOKEN.
    $names = @($p.AppEnvironmentExtra | ForEach-Object { ($_ -split '=', 2)[0] })
    Write-Host "  Environment      $($names -join ', ')"
    Write-Host ""
}


try {
    $script:nssm = Get-NssmPath

    if ($Show) { Show-ServiceSettings; exit 0 }

    Assert-PinakesAdmin
    if ($Threads -lt 1 -or $Threads -gt 64) { throw "-Threads must be between 1 and 64." }
    if (-not (Test-Path $PinakesLogDir)) { New-Item -ItemType Directory -Path $PinakesLogDir -Force | Out-Null }

    Write-PinakesLog "Current settings" 'Step'
    Show-ServiceSettings

    $current = [string](Get-ItemProperty -Path $paramsKey).AppParameters
    $newArgs = @(Get-ListenArgs -Current $current) + @(
        "--threads=$Threads",
        "--channel-timeout=$ChannelTimeoutSec",
        'app:app'
    )
    $newParams = $newArgs -join ' '

    Write-PinakesLog "Applying" 'Step'
    Invoke-Nssm @('set', $PinakesService, 'AppParameters', $newParams)
    Write-PinakesLog "AppParameters = $newParams" 'Success'

    Invoke-Nssm @('set', $PinakesService, 'AppStdout', $stdoutLog)
    Invoke-Nssm @('set', $PinakesService, 'AppStderr', $stderrLog)
    Invoke-Nssm @('set', $PinakesService, 'AppRotateFiles', '1')
    Invoke-Nssm @('set', $PinakesService, 'AppRotateOnline', '1')
    Invoke-Nssm @('set', $PinakesService, 'AppRotateSeconds', '86400')
    Invoke-Nssm @('set', $PinakesService, 'AppRotateBytes', '10485760')
    Write-PinakesLog "Logs -> $stdoutLog, $stderrLog (rotated daily or at 10 MB)" 'Success'

    Invoke-Nssm @('set', $PinakesService, 'AppExit', 'Default', 'Restart')
    Write-PinakesLog "AppExit = Restart" 'Success'

    # Append, never replace: this value also holds DB_PATH and the admin token,
    # and `nssm set AppEnvironmentExtra` would overwrite every line.
    $envLines = @((Get-ItemProperty -Path $paramsKey).AppEnvironmentExtra | Where-Object { $_ })
    if (-not ($envLines | Where-Object { $_ -like 'PYTHONUNBUFFERED=*' })) {
        $envLines += 'PYTHONUNBUFFERED=1'
        Set-ItemProperty -Path $paramsKey -Name 'AppEnvironmentExtra' -Value ([string[]]$envLines) -Type MultiString
        Write-PinakesLog "Added PYTHONUNBUFFERED=1 to the service environment ($($envLines.Count) lines kept)." 'Success'
    } else {
        Write-PinakesLog "PYTHONUNBUFFERED already set."
    }

    if ($NoRestart) {
        Write-PinakesLog "Not restarting (-NoRestart). The settings apply at the next restart." 'Warn'
        exit 0
    }

    Write-PinakesLog "Restarting" 'Step'
    Stop-PinakesService
    Start-PinakesService
    $health = Wait-PinakesHealthy -TimeoutSec 180
    if ($null -eq $health) {
        Write-PinakesLog "The service did not answer /health within 180s. Read $stderrLog." 'Error'
        if (Test-Path $stderrLog) { Get-Content $stderrLog -Tail 40 }
        exit 1
    }
    Write-PinakesLog "Healthy on $($health.version) with $Threads threads." 'Success'
    if (Test-Path $stderrLog) {
        Write-PinakesLog "Logging works: $stderrLog is $((Get-Item $stderrLog).Length) bytes." 'Success'
    }
    exit 0

} catch {
    Write-PinakesLog $_.Exception.Message 'Error'
    exit 1
}
