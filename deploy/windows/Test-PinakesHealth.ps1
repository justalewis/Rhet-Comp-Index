#Requires -Version 5.1
<#
.SYNOPSIS
    Smoke-tests a Pinakes deployment and prints a pass/fail summary.

.DESCRIPTION
    Two modes.

    Local (default) - run on the server itself. Checks the Windows service,
    the app on the loopback address, the database, the public URL through IIS,
    and whether the checked-out code has fallen behind GitHub.

    Remote (-Remote) - run from anywhere, including a laptop with no access to
    the server. Performs only the checks that are visible over HTTPS. This is
    the mode to use when you want to confirm from outside that a deploy landed.

    Every check prints PASS, FAIL, or WARN, and the script exits non-zero if
    any check FAILs, so it can be wired into monitoring later.

    Two checks are worth explaining, because they cover problems that are
    invisible if you only look at status codes:

    "API responses are not shared-cacheable" - IIS/ARR keeps its own disk
    cache and honours Cache-Control: public. When the API sent `public`, ARR
    pinned copies of every stats and citations endpoint for an hour, so
    requests never reached Flask. During the initial seed that meant the site
    served empty JSON from cache while the database behind it was filling up.
    The app now sends `private` (web_helpers.cache_response); this check is
    the regression guard.

    "Public and local versions agree" - catches a stale cached copy of the
    site being served by the proxy after a deploy, and catches the working
    tree having been updated without the service being restarted.

.PARAMETER Remote
    HTTPS-only checks against the public URL. No service, git, or filesystem
    access needed.

.PARAMETER BaseUrl
    Override the URL being tested. In remote mode this defaults to
    PINAKES_SITE_URL, otherwise https://pinakes.wacclearinghouse.org.

.EXAMPLE
    .\Test-PinakesHealth.ps1
    Full check, run on the server.

.EXAMPLE
    .\Test-PinakesHealth.ps1 -Remote
    From a laptop: is the public site healthy, and what version is it running?

.EXAMPLE
    .\Test-PinakesHealth.ps1 -Remote -BaseUrl https://pinakes.xyz
#>

[CmdletBinding()]
param(
    [switch]$Remote,
    [string]$BaseUrl
)

$ErrorActionPreference = 'Stop'

# In remote mode this script must run on a machine that has none of the
# Pinakes layout, so the shared helpers are optional there.
$commonPath = Join-Path $PSScriptRoot 'Pinakes.Common.ps1'
$haveCommon = $false
if (Test-Path $commonPath) {
    try { . $commonPath; $haveCommon = $true } catch { $haveCommon = $false }
}
if (-not $haveCommon) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $PinakesPublicUrl = 'https://pinakes.wacclearinghouse.org'
}

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    if ($Remote -or -not $haveCommon) { $BaseUrl = $PinakesPublicUrl } else { $BaseUrl = $PinakesLocalUrl }
}

$script:Failures = 0
$script:Warnings = 0

function Test-Result {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet('PASS', 'FAIL', 'WARN', 'INFO')][string]$Status,
        [string]$Detail = ''
    )
    $colour = 'Gray'
    switch ($Status) {
        'PASS' { $colour = 'Green' }
        'FAIL' { $colour = 'Red';    $script:Failures++ }
        'WARN' { $colour = 'Yellow'; $script:Warnings++ }
    }
    Write-Host ("  [{0}] " -f $Status) -ForegroundColor $colour -NoNewline
    Write-Host ("{0,-42} {1}" -f $Name, $Detail)
}

function Invoke-Probe {
    <#  GET a path and return status, headers and body without throwing.
        Appends a cache-buster so an intermediate proxy cannot answer for the
        app - the entire point of several of these checks. #>
    param([string]$Path, [int]$TimeoutSec = 30)
    $sep = '?'
    if ($Path.Contains('?')) { $sep = '&' }
    $uri = "$BaseUrl$Path$sep" + "_cb=" + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    try {
        $r = Invoke-WebRequest -Uri $uri -TimeoutSec $TimeoutSec -UseBasicParsing
        return [PSCustomObject]@{ Ok = $true; Status = [int]$r.StatusCode; Headers = $r.Headers; Body = $r.Content; Error = $null }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return [PSCustomObject]@{ Ok = $false; Status = $status; Headers = $null; Body = $null; Error = $_.Exception.Message }
    }
}


Write-Host ""
Write-Host "Pinakes health check" -ForegroundColor Cyan
Write-Host "Target: $BaseUrl"
if ($Remote) { Write-Host "Mode:   remote (HTTPS checks only)" } else { Write-Host "Mode:   local (full)" }
Write-Host ""

# -- Server-side checks ------------------------------------------------------

$localVersion = $null

if (-not $Remote -and $haveCommon) {

    $svc = Get-Service -Name $PinakesService -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Test-Result "Service '$PinakesService' exists" 'FAIL' "not registered - see WINDOWS-SERVER-INSTALL.md 8.2"
    } elseif ($svc.Status -eq 'Running') {
        Test-Result "Service '$PinakesService' running" 'PASS'
    } else {
        Test-Result "Service '$PinakesService' running" 'FAIL' "state is $($svc.Status)"
    }

    if (Test-Path $PinakesDbPath) {
        $sizeMb = [Math]::Round((Get-Item $PinakesDbPath).Length / 1MB, 1)
        if ($sizeMb -lt 1) {
            Test-Result "Database file present" 'WARN' "$PinakesDbPath is only $sizeMb MB - has the initial fetch run?"
        } else {
            Test-Result "Database file present" 'PASS' "$sizeMb MB"
        }
    } else {
        Test-Result "Database file present" 'FAIL' "no file at $PinakesDbPath"
    }

    try {
        $localHealth = Invoke-RestMethod -Uri "$PinakesLocalUrl/health" -TimeoutSec 15 -UseBasicParsing
        $localVersion = $localHealth.version
        Test-Result "App answers on loopback" 'PASS' "version $localVersion, up $([Math]::Round($localHealth.uptime_seconds / 3600, 1))h"
        if ($localHealth.admin_auth -eq 'configured') {
            Test-Result "Admin token configured" 'PASS'
        } else {
            Test-Result "Admin token configured" 'FAIL' "PINAKES_ADMIN_TOKEN missing from the service environment; scheduled jobs will 503"
        }
    } catch {
        Test-Result "App answers on loopback" 'FAIL' $_.Exception.Message
    }

    # Version drift against GitHub.
    try {
        Invoke-PinakesGit -Arguments @('fetch', $PinakesGitRemote, '--quiet') -AllowFailure | Out-Null
        $head   = Get-PinakesShortCommit 'HEAD'
        $origin = Get-PinakesShortCommit "$PinakesGitRemote/$PinakesGitBranch"
        if ($head -eq $origin) {
            Test-Result "Code matches GitHub" 'PASS' "$head"
        } else {
            $behind = (Invoke-PinakesGit -Arguments @('rev-list', '--count', "HEAD..$PinakesGitRemote/$PinakesGitBranch")).Output.Trim()
            Test-Result "Code matches GitHub" 'WARN' "$behind commit(s) behind ($head vs $origin) - run Update-Pinakes.ps1"
        }
        if ($null -ne $localVersion -and $localVersion -ne $head) {
            Test-Result "Service running checked-out code" 'FAIL' "tree is $head, service is running $localVersion - restart '$PinakesService'"
        } elseif ($null -ne $localVersion) {
            Test-Result "Service running checked-out code" 'PASS'
        }
    } catch {
        Test-Result "Code matches GitHub" 'WARN' $_.Exception.Message
    }
}

# -- HTTP checks (both modes) ------------------------------------------------

$health = Invoke-Probe '/health'
$publicVersion = $null
if ($health.Ok) {
    $payload = $health.Body | ConvertFrom-Json
    $publicVersion = $payload.version
    Test-Result "GET /health" 'PASS' "version $publicVersion, status $($payload.status)"
} else {
    Test-Result "GET /health" 'FAIL' "HTTP $($health.Status) $($health.Error)"
}

$ready = Invoke-Probe '/health/ready'
if ($ready.Ok) {
    $payload = $ready.Body | ConvertFrom-Json
    if ($payload.db -eq 'reachable') {
        Test-Result "GET /health/ready (database)" 'PASS' "db $($payload.db)"
    } else {
        Test-Result "GET /health/ready (database)" 'FAIL' "db $($payload.db)"
    }
} else {
    Test-Result "GET /health/ready (database)" 'FAIL' "HTTP $($ready.Status)"
}

if ($null -ne $localVersion -and $null -ne $publicVersion) {
    if ($localVersion -eq $publicVersion) {
        Test-Result "Public and local versions agree" 'PASS' $publicVersion
    } else {
        Test-Result "Public and local versions agree" 'FAIL' "public $publicVersion vs local $localVersion - the proxy is serving a stale copy"
    }
}

# Content pages.
foreach ($page in @('/', '/tools', '/explore', '/about')) {
    $r = Invoke-Probe $page
    if ($r.Ok -and $r.Status -eq 200) {
        Test-Result "GET $page" 'PASS' "$([Math]::Round($r.Body.Length / 1024, 1)) KB"
    } else {
        Test-Result "GET $page" 'FAIL' "HTTP $($r.Status) $($r.Error)"
    }
}

# Shared-cacheability regression guard. See the note in the help text above.
$api = Invoke-Probe '/api/stats/timeline'
if ($api.Ok) {
    $cc = ''
    if ($api.Headers -and $api.Headers['Cache-Control']) { $cc = [string]$api.Headers['Cache-Control'] }
    if ($cc -match 'public') {
        Test-Result "API responses not shared-cacheable" 'FAIL' "Cache-Control: $cc - IIS/ARR will serve stale copies"
    } elseif ($cc -match 'private') {
        Test-Result "API responses not shared-cacheable" 'PASS' "Cache-Control: $cc"
    } else {
        Test-Result "API responses not shared-cacheable" 'WARN' "unexpected Cache-Control: '$cc'"
    }
} else {
    Test-Result "API responses not shared-cacheable" 'FAIL' "HTTP $($api.Status)"
}

# Does the index actually hold anything? A brand-new install answers 200
# everywhere while being completely empty, which is the failure mode that
# looks most like success.
$articles = Invoke-Probe '/api/articles?limit=1'
if ($articles.Ok) {
    $payload = $articles.Body | ConvertFrom-Json
    $total = 0
    if ($payload.PSObject.Properties.Name -contains 'total') { $total = [int]$payload.total }
    if ($total -gt 0) {
        Test-Result "Index contains articles" 'PASS' "$total articles"
    } else {
        Test-Result "Index contains articles" 'WARN' "0 articles - the fetch has not populated the database yet"
    }
} else {
    Test-Result "Index contains articles" 'FAIL' "HTTP $($articles.Status)"
}

# -- Summary -----------------------------------------------------------------

Write-Host ""
if ($script:Failures -gt 0) {
    Write-Host "$($script:Failures) check(s) FAILED, $($script:Warnings) warning(s)." -ForegroundColor Red
    exit 1
}
if ($script:Warnings -gt 0) {
    Write-Host "All checks passed, with $($script:Warnings) warning(s)." -ForegroundColor Yellow
    exit 0
}
Write-Host "All checks passed." -ForegroundColor Green
exit 0
