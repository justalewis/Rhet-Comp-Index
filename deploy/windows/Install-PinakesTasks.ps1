#Requires -Version 5.1
<#
.SYNOPSIS
    Registers the Windows scheduled tasks that keep Pinakes fed and backed up.

.DESCRIPTION
    Replaces the "Pinakes-Scheduler" service described in section 8.3 of
    docs/WINDOWS-SERVER-INSTALL.md. That service was defined to run
    scheduler.py, which no longer exists: it was deleted in commit fa25cae
    ("Replace standalone scheduler with cron-driven admin endpoints"). On the
    Fly deployment those jobs are driven by GitHub Actions hitting pinakes.xyz
    (.github/workflows/cron.yml and friends), which does nothing for this
    server. These tasks are this server's equivalent.

    Tasks registered (all run as SYSTEM, all start if the server was off):

      Pinakes-NightlyBackup      02:00 daily     local SQLite backup
      Pinakes-DailyFetch         03:23 daily     incremental CrossRef/RSS fetch
      Pinakes-Prewarm            04:15 daily     rebuild cached aggregates
      Pinakes-WeeklyMaintenance  04:45 Sundays   weekly maintenance
      Pinakes-WeeklyDigests      14:17 Fridays   saved-search emails (opt-in)
      Pinakes-UpdateCheck        08:00 daily     report GitHub drift (opt-in)

    Backup runs before the fetch so there is always a copy of yesterday's good
    database from before the day's writes. Prewarm runs after the fetch so it
    warms the new data rather than the old.

    Safe to re-run: existing tasks with these names are replaced.

.PARAMETER IncludeDigests
    Also register the weekly saved-search digest task. Leave this off unless
    PINAKES_ALERTS_ENABLED=1 and the sending domain is verified - otherwise
    the endpoint is a no-op and the task is noise.

.PARAMETER IncludeUpdateCheck
    Also register a daily task that runs Update-Pinakes.ps1 -Check and logs
    whether this server has fallen behind GitHub. It only REPORTS; it never
    deploys. Recommended - version drift is how this server ended up running
    a fetch that CrossRef had started rejecting.

.PARAMETER List
    Show the current state of these tasks and exit. Changes nothing.

.PARAMETER Remove
    Unregister all of these tasks and exit.

.EXAMPLE
    .\Install-PinakesTasks.ps1 -IncludeUpdateCheck

.EXAMPLE
    .\Install-PinakesTasks.ps1 -List
#>

[CmdletBinding()]
param(
    [switch]$IncludeDigests,
    [switch]$IncludeUpdateCheck,
    [switch]$List,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Pinakes.Common.ps1"

$adminScript  = Join-Path $PSScriptRoot 'Invoke-PinakesAdmin.ps1'
$backupScript = Join-Path $PSScriptRoot 'Backup-Pinakes.ps1'
$updateScript = Join-Path $PSScriptRoot 'Update-Pinakes.ps1'

$AllTaskNames = @(
    'Pinakes-NightlyBackup',
    'Pinakes-DailyFetch',
    'Pinakes-Prewarm',
    'Pinakes-WeeklyMaintenance',
    'Pinakes-WeeklyDigests',
    'Pinakes-UpdateCheck'
)


function Show-PinakesTasks {
    Write-PinakesLog "Scheduled task status" 'Step'
    foreach ($name in $AllTaskNames) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            Write-Host ("  {0,-27} not registered" -f $name) -ForegroundColor DarkGray
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskName $name
        $last = "never"
        if ($info.LastRunTime -and $info.LastRunTime.Year -gt 1980) {
            $last = $info.LastRunTime.ToString('yyyy-MM-dd HH:mm')
        }
        $result = "  (last result: 0x{0:X})" -f $info.LastTaskResult
        if ($info.LastTaskResult -eq 0) { $result = "" }
        Write-Host ("  {0,-27} {1,-9} last run {2}{3}" -f $name, $task.State, $last, $result)
    }
    Write-Host ""
    Write-Host "  Job log: $(Join-Path $PinakesLogDir 'admin-tasks.log')"
}


function Register-PinakesTask {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string]$ScriptArgs = '',
        [Parameter(Mandatory = $true)]$Trigger,
        [Parameter(Mandatory = $true)][string]$Description,
        [int]$TimeLimitHours = 6
    )

    if (-not (Test-Path $ScriptPath)) { throw "Script not found: $ScriptPath" }

    $argument = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
    if (-not [string]::IsNullOrWhiteSpace($ScriptArgs)) { $argument = "$argument $ScriptArgs" }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $PSScriptRoot

    # SYSTEM, because that is what reads Machine-scope environment variables -
    # which is where PINAKES_ADMIN_TOKEN and DB_PATH live, and the whole reason
    # the token never has to be written into a task definition.
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours $TimeLimitHours)

    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Principal $principal -Settings $settings -Description $Description -Force | Out-Null

    Write-PinakesLog "Registered $Name" 'Success'
}


try {
    if ($List) { Show-PinakesTasks; exit 0 }

    Assert-PinakesAdmin

    if ($Remove) {
        Write-PinakesLog "Removing Pinakes scheduled tasks" 'Step'
        foreach ($name in $AllTaskNames) {
            if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
                Write-PinakesLog "Removed $name" 'Success'
            }
        }
        exit 0
    }

    Assert-PinakesPrereqs

    Write-PinakesLog "Registering Pinakes scheduled tasks" 'Step'
    Write-PinakesLog "Scripts: $PSScriptRoot"

    Register-PinakesTask -Name 'Pinakes-NightlyBackup' `
        -ScriptPath $backupScript -ScriptArgs '-Label nightly' `
        -Trigger (New-ScheduledTaskTrigger -Daily -At '02:00') `
        -Description 'Local SQLite backup of the Pinakes database, before the day''s fetch writes to it.' `
        -TimeLimitHours 2

    Register-PinakesTask -Name 'Pinakes-DailyFetch' `
        -ScriptPath $adminScript -ScriptArgs '-Endpoint Fetch' `
        -Trigger (New-ScheduledTaskTrigger -Daily -At '03:23') `
        -Description 'Incremental CrossRef/RSS article fetch. Replaces the cron.yml job that targets pinakes.xyz.'

    Register-PinakesTask -Name 'Pinakes-Prewarm' `
        -ScriptPath $adminScript -ScriptArgs '-Endpoint Prewarm' `
        -Trigger (New-ScheduledTaskTrigger -Daily -At '04:15') `
        -Description 'Rebuild cached aggregates after the daily fetch.' `
        -TimeLimitHours 2

    Register-PinakesTask -Name 'Pinakes-WeeklyMaintenance' `
        -ScriptPath $adminScript -ScriptArgs '-Endpoint Maintenance' `
        -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '04:45') `
        -Description 'Weekly maintenance pass (weekly_maintenance.py).'

    if ($IncludeDigests) {
        Register-PinakesTask -Name 'Pinakes-WeeklyDigests' `
            -ScriptPath $adminScript -ScriptArgs '-Endpoint Digests' `
            -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At '14:17') `
            -Description 'Saved-search email digests. No-op unless PINAKES_ALERTS_ENABLED=1.' `
            -TimeLimitHours 2
    } else {
        Write-PinakesLog "Skipping Pinakes-WeeklyDigests (pass -IncludeDigests to register it)."
    }

    if ($IncludeUpdateCheck) {
        Register-PinakesTask -Name 'Pinakes-UpdateCheck' `
            -ScriptPath $updateScript -ScriptArgs '-Check' `
            -Trigger (New-ScheduledTaskTrigger -Daily -At '08:00') `
            -Description 'Reports whether this server has fallen behind GitHub. Reports only; never deploys.' `
            -TimeLimitHours 1
    } else {
        Write-PinakesLog "Skipping Pinakes-UpdateCheck (pass -IncludeUpdateCheck to register it)."
    }

    Write-Host ""
    Show-PinakesTasks

    Write-PinakesLog "Done. Verify one now with: Start-ScheduledTask -TaskName Pinakes-DailyFetch" 'Success'
    exit 0

} catch {
    Write-PinakesLog $_.Exception.Message 'Error'
    exit 1
}
