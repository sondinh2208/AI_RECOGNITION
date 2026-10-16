[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SimulateDisplay
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$interfaceOutput = netsh wlan show interfaces
$ssidLine = $interfaceOutput | Where-Object {
    $_ -match '^\s*SSID\s*:'
} | Select-Object -First 1

if (-not $ssidLine) {
    throw "Khong tim thay ket noi Wi-Fi dang hoat dong."
}

$ssid = ($ssidLine -split ':', 2)[1].Trim()
if ($ssid -cne "Sonb") {
    throw "May tinh dang ket noi '$ssid', khong phai mang Sonb."
}

$wifiAddress = Get-NetIPAddress `
    -InterfaceAlias "Wi-Fi" `
    -AddressFamily IPv4 `
    -ErrorAction Stop |
    Where-Object { $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1

if (-not $wifiAddress) {
    throw "Khong lay duoc dia chi IPv4 cua Wi-Fi Sonb."
}

$env:FACECHECK_DISPLAY_HOST = $wifiAddress.IPAddress
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Khong tim thay Python cua du an tai $python"
}

Write-Host "[SONB] Wi-Fi: $ssid"
Write-Host "[SONB] FaceCheck host: $($wifiAddress.IPAddress)"

if ($CheckOnly) {
    exit 0
}

$launcherArgs = @(
    (Join-Path $PSScriptRoot "facecheck.py"),
    "wifi",
    "--camera-host",
    "facecheck-cam.local"
)
if ($SimulateDisplay) {
    $launcherArgs += "--simulate-display"
}

& $python @launcherArgs
exit $LASTEXITCODE
