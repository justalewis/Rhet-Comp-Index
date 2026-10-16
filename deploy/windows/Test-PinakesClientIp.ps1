#Requires -Version 5.1
<#
.SYNOPSIS
    Checks that Pinakes sees each visitor's real IP address through the
    Cloudflare -> IIS/ARR -> Waitress chain.

.DESCRIPTION
    Run from any machine, through the public hostname. Nothing here works
    against 127.0.0.1: the point is to test the whole proxy chain.

    The rate limiter, the IP denylist, the community-tag one-vote-per-reader
    rule and every IP in the logs all depend on the app knowing who is asking.
    On the Clearinghouse box it did not: Waitress 3.x deletes X-Forwarded-For
    before Flask sees it, so every request looked like it came from IIS on
    127.0.0.1, and the whole site shared one rate-limit budget. The fix is
    PINAKES_TRUSTED_IP_HEADER=CF-Connecting-IP in the service environment.

    Checks, in order:

    "Cloudflare sees this machine as" - reads /cdn-cgi/trace over IPv4 and,
    when this machine has it, IPv6. Those are the addresses the app should
    report.

    "Separate visitors get separate budgets" - needs no token. Sends requests
    over IPv4 and IPv6, which Cloudflare sees as two different visitors, and
    reads X-RateLimit-Remaining. If the IPv6 request is charged to the IPv4
    budget, the app cannot tell visitors apart.

    "Forged X-Forwarded-For is ignored" - a request carrying a made-up
    X-Forwarded-For must be charged to the same budget as an honest one.

    "App reports the visitor IP" - needs the admin token. Calls
    /api/admin/client-ip and compares its answer with what Cloudflare saw. It
    also shows the PINAKES_TRUSTED_IP_HEADER value the running service has,
    which confirms an environment change reached the process, and what each
    candidate header would produce.

.PARAMETER BaseUrl
    The public URL. Defaults to PINAKES_SITE_URL, else
    https://pinakes.wacclearinghouse.org.

.EXAMPLE
    .\Test-PinakesClientIp.ps1
    Runs the token-free checks, plus the endpoint check if PINAKES_ADMIN_TOKEN
    is set in this session or at Machine scope.

.EXAMPLE
    $env:PINAKES_ADMIN_TOKEN = '<token>'; .\Test-PinakesClientIp.ps1
    From a laptop, with the token for this session only.
#>

[CmdletBinding()]
param(
    [string]$BaseUrl
)

$ErrorActionPreference = 'Stop'

$commonPath = Join-Path $PSScriptRoot 'Pinakes.Common.ps1'
$haveCommon = $false
if (Test-Path $commonPath) {
    try { . $commonPath; $haveCommon = $true } catch { $haveCommon = $false }
}
if (-not $haveCommon) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $PinakesPublicUrl = 'https://pinakes.wacclearinghouse.org'
}
if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = $PinakesPublicUrl }
$BaseUrl = $BaseUrl.TrimEnd('/')

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

function ConvertTo-CanonicalIp {
    <#  One spelling per address, so IPv6 written two ways still compares
        equal. Returns the input unchanged if it is not an address. #>
    param([string]$Value)
    $parsed = $null
    if ([System.Net.IPAddress]::TryParse($Value.Trim(), [ref]$parsed)) { return $parsed.ToString() }
    return $Value.Trim()
}

function Get-TraceIp {
    <#  The client IP Cloudflare recorded, over one address family, or $null. #>
    param([ValidateSet('4', '6')][string]$Family)
    $ErrorActionPreference = 'Continue'
    $out = & curl.exe -s "-$Family" -m 15 "$BaseUrl/cdn-cgi/trace"
    if ($LASTEXITCODE -ne 0) { return $null }
    foreach ($line in $out) {
        if ($line -match '^ip=(.+)$') { return ConvertTo-CanonicalIp $Matches[1] }
    }
    return $null
}

function Get-Remaining {
    <#  X-RateLimit-Remaining for one GET of a cheap, rate-limited page, or
        $null. The cache-buster keeps Cloudflare and ARR from answering. #>
    param([ValidateSet('4', '6')][string]$Family, [string[]]$ExtraHeaders = @())
    $ErrorActionPreference = 'Continue'
    $uri = "$BaseUrl/glossary?_cb=" + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $curlArgs = @('-s', "-$Family", '-m', '20', '-o', 'NUL', '-D', '-')
    foreach ($h in $ExtraHeaders) { $curlArgs += @('-H', $h) }
    $out = & curl.exe @curlArgs $uri
    if ($LASTEXITCODE -ne 0) { return $null }
    foreach ($line in $out) {
        if ($line -match '^x-ratelimit-remaining:\s*(\d+)') { return [int]$Matches[1] }
    }
    return $null
}


Write-Host ""
Write-Host "Pinakes client-IP check" -ForegroundColor Cyan
Write-Host "Target: $BaseUrl"
Write-Host ""

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    Test-Result "curl.exe available" 'FAIL' "ships with Windows 10 1803+ / Server 2019+; needed to pick IPv4 or IPv6"
    exit 1
}

# -- What Cloudflare sees ----------------------------------------------------

$ip4 = Get-TraceIp '4'
$ip6 = Get-TraceIp '6'
if ($null -eq $ip4 -and $null -eq $ip6) {
    Test-Result "Cloudflare sees this machine as" 'FAIL' "no answer from $BaseUrl/cdn-cgi/trace - is the site behind Cloudflare?"
    exit 1
}
$seen = @(@($ip4, $ip6) | Where-Object { $_ })
Test-Result "Cloudflare sees this machine as" 'INFO' ($seen -join '  and  ')

# -- Token-free: rate-limit budgets ------------------------------------------

if ($null -ne $ip4) {
    $a = Get-Remaining '4'
    $b = Get-Remaining '4'
    $v6 = $null
    if ($null -ne $ip6) { $v6 = Get-Remaining '6' }
    $c = Get-Remaining '4'
    $forged = Get-Remaining '4' @('X-Forwarded-For: 198.51.100.7')
    $d = Get-Remaining '4'

    if ($null -eq $a -or $null -eq $b -or $null -eq $c) {
        Test-Result "Separate visitors get separate budgets" 'WARN' "no X-RateLimit-Remaining header on /glossary - cannot tell"
    } elseif ($b -ne ($a - 1)) {
        Test-Result "Separate visitors get separate budgets" 'WARN' "IPv4 budget went $a -> $b (window reset or other traffic); rerun"
    } elseif ($null -eq $v6) {
        Test-Result "Separate visitors get separate budgets" 'INFO' "no IPv6 here, so only one visitor to test with; rely on the endpoint check"
    } elseif ($c -eq ($b - 1)) {
        Test-Result "Separate visitors get separate budgets" 'PASS' "IPv4 $a -> $b -> $c, IPv6 counted separately ($v6)"
    } else {
        Test-Result "Separate visitors get separate budgets" 'FAIL' "IPv4 $b -> $c around one IPv6 request ($v6): both are charged to one budget, so the app is not seeing visitor IPs"
    }

    if ($null -ne $forged -and $null -ne $c -and $null -ne $d) {
        if ($forged -eq ($c - 1) -and $d -eq ($forged - 1)) {
            Test-Result "Forged X-Forwarded-For is ignored" 'PASS' "charged to the same budget ($c -> $forged -> $d)"
        } elseif ($d -eq ($c - 1)) {
            Test-Result "Forged X-Forwarded-For is ignored" 'FAIL' "the forged request got its own budget ($forged); a client can pick its own identity"
        } else {
            Test-Result "Forged X-Forwarded-For is ignored" 'WARN' "budgets moved unexpectedly ($c, $forged, $d); rerun"
        }
    }
}

# -- With the admin token: what the app itself reports -----------------------

$token = $env:PINAKES_ADMIN_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    $token = [Environment]::GetEnvironmentVariable('PINAKES_ADMIN_TOKEN', 'Machine')
}
if ([string]::IsNullOrWhiteSpace($token)) {
    Test-Result "App reports the visitor IP" 'INFO' "skipped - set `$env:PINAKES_ADMIN_TOKEN to run it"
} else {
    $report = $null
    try {
        $report = Invoke-RestMethod -Uri "$BaseUrl/api/admin/client-ip" -TimeoutSec 20 -UseBasicParsing `
            -Headers @{ Authorization = "Bearer $token" }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        switch ($status) {
            404     { Test-Result "App reports the visitor IP" 'WARN' "404 - this server is running code from before /api/admin/client-ip; deploy first" }
            403     { Test-Result "App reports the visitor IP" 'WARN' "403 - that token does not match the service's PINAKES_ADMIN_TOKEN" }
            default { Test-Result "App reports the visitor IP" 'FAIL' "HTTP $status $($_.Exception.Message)" }
        }
    }
    if ($null -ne $report) {
        if ($seen -contains (ConvertTo-CanonicalIp ([string]$report.ip))) {
            Test-Result "App reports the visitor IP" 'PASS' "$($report.ip) via $($report.source)"
        } else {
            Test-Result "App reports the visitor IP" 'FAIL' "app says $($report.ip) via $($report.source); Cloudflare says $($seen -join ' / ')"
        }

        $configured = $report.trusted_ip_header
        if ([string]::IsNullOrWhiteSpace($configured)) { $configured = '(unset)' }
        Test-Result "PINAKES_TRUSTED_IP_HEADER in the service" 'INFO' $configured
        Test-Result "Socket peer" 'INFO' "$($report.socket_peer) (local proxy: $($report.socket_peer_is_local_proxy))"
        foreach ($p in $report.candidates.PSObject.Properties) {
            $v = $p.Value
            if ($null -eq $v) { $v = '-' }
            Test-Result ("  if set to {0}" -f $p.Name) 'INFO' $v
        }

        # Emailed links (name-removal verification, alert digests) are built
        # from the request host. Waitress strips X-Forwarded-Host as well.
        $ext = [string]$report.external_url
        if ($ext -match '^https://' -and $ext -notmatch '127\.0\.0\.1|localhost') {
            Test-Result "Absolute URLs use the public host" 'PASS' $ext
        } else {
            Test-Result "Absolute URLs use the public host" 'WARN' "$ext - emailed links would point here; see WINDOWS-SERVER-RUNBOOK.md"
        }
    }
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
