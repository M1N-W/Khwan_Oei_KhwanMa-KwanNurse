# Phase 4 production smoke test
# Usage: .\scripts\smoke_test_phase4.ps1
$ErrorActionPreference = "Continue"
$base = "https://khwan-oei-khwanma.onrender.com"

function Show-Test($name, $ok, $detail) {
    $mark = if ($ok) { "[PASS]" } else { "[FAIL]" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host "$mark $name" -ForegroundColor $color
    if ($detail) { Write-Host "       $detail" -ForegroundColor DarkGray }
}

Write-Host "`n=== Phase 4 Smoke Test: $base ===" -ForegroundColor Cyan

# 1. /healthz alive
$r = Invoke-WebRequest -Uri "$base/healthz" -UseBasicParsing
Show-Test "/healthz returns 200" ($r.StatusCode -eq 200) "body=$($r.Content.Trim())"

# 2. P4-2: X-Request-ID header (auto-generated). PowerShell may return
#    headers as a string[] so coerce with [string] before length check.
$rid = [string]($r.Headers['X-Request-ID'])
Show-Test "P4-2: X-Request-ID auto-generated" ($rid.Length -ge 16) "rid=$rid"

# 3. P4-2: inbound X-Request-ID echoed back
$myRid = "smoke-test-" + (Get-Random)
$r2 = Invoke-WebRequest -Uri "$base/healthz" -Headers @{"X-Request-ID"=$myRid} -UseBasicParsing
$echoRid = [string]($r2.Headers['X-Request-ID'])
Show-Test "P4-2: inbound X-Request-ID echoed" ($echoRid -eq $myRid) "echo=$echoRid"

# 4. P4-3: /readyz with sheets probe
$r3 = Invoke-WebRequest -Uri "$base/readyz" -UseBasicParsing
$ready = $r3.Content | ConvertFrom-Json
Show-Test "P4-3: /readyz sheets reachable" ($ready.status -eq "ready") "checks.sheets=$($ready.checks.sheets)"

# 5. P4-1: /line/webhook signature enforcement
try {
    $r4 = Invoke-WebRequest -Uri "$base/line/webhook" -Method POST -Body '{"events":[]}' -ContentType "application/json" -UseBasicParsing -ErrorAction Stop
    $code = $r4.StatusCode
} catch {
    $code = $_.Exception.Response.StatusCode.value__
}
$secEnforced = ($code -eq 401)
Show-Test "P4-1: /line/webhook rejects unsigned (HTTP 401)" $secEnforced "got HTTP $code (expected 401 once LINE_CHANNEL_SECRET is set)"

# 6. /metrics — security counters
$r5 = Invoke-WebRequest -Uri "$base/metrics" -UseBasicParsing
$m = ($r5.Content | ConvertFrom-Json).counters
$noSecret = $m.PSObject.Properties['security.line.no_secret_configured']
$valid = $m.PSObject.Properties['security.line.signature_valid']
$invalid = $m.PSObject.Properties['security.line.signature_invalid']

Write-Host "`n--- security counters ---" -ForegroundColor Cyan
if ($noSecret) { Write-Host "  security.line.no_secret_configured = $($noSecret.Value)  <- WARNING: secret not set" -ForegroundColor Yellow }
if ($valid)    { Write-Host "  security.line.signature_valid       = $($valid.Value)" -ForegroundColor Green }
if ($invalid)  { Write-Host "  security.line.signature_invalid     = $($invalid.Value)" -ForegroundColor Yellow }

Write-Host "`n--- summary ---" -ForegroundColor Cyan
if ($secEnforced) {
    Write-Host "All security checks PASSED. Ready for production traffic." -ForegroundColor Green
} else {
    Write-Host "ACTION REQUIRED: Set LINE_CHANNEL_SECRET on Render env vars." -ForegroundColor Yellow
    Write-Host "  Source: LINE Developer Console -> Channel -> Basic settings -> Channel secret"
}
