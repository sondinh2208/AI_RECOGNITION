#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

# Explicitly authorized pair for the current Dinh Duc network only.
$displayRules = @(
    @{Name='FaceCheck-TFT-COM15-CurrentNetwork-TCP'; Protocol='TCP'; Port=8767},
    @{Name='FaceCheck-TFT-COM15-CurrentNetwork-UDP'; Protocol='UDP'; Port=8768}
)
foreach ($displayRule in $displayRules) {
    if (-not (Get-NetFirewallRule -Name $displayRule.Name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $displayRule.Name `
            -DisplayName $displayRule.Name -Direction Inbound -Action Allow `
            -Program 'C:\Program Files\Python311\python.exe' `
            -Protocol $displayRule.Protocol -LocalPort $displayRule.Port `
            -LocalAddress '192.168.1.11' -RemoteAddress '192.168.1.3' `
            -Profile Public,Private | Out-Null
    }
}
