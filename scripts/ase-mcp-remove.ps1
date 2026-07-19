# ase-mcp-remove.ps1
# Remove the ase-mcp portproxy + firewall rule. Run as Administrator.

$Port     = 8001
$RuleName = "ase MCP"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[FAIL] Please run this script as Administrator." -ForegroundColor Red
    exit 1
}

Write-Host "Removing ase-mcp portproxy + firewall rules..." -ForegroundColor Cyan

# Delete every portproxy listener for this port, whatever the listen address.
$rows = netsh interface portproxy show v4tov4 | Select-String -SimpleMatch ":$Port"
foreach ($row in $rows) {
    $listen = ($row -split '\s+') | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } | Select-Object -First 1
    if ($listen) {
        netsh interface portproxy delete v4tov4 listenaddress=$listen listenport=$Port 2>$null | Out-Null
        Write-Host "  removed portproxy $listen`:$Port" -ForegroundColor Green
    }
}

netsh advfirewall firewall delete rule name="$RuleName" 2>$null | Out-Null
Write-Host "  removed firewall rule '$RuleName'" -ForegroundColor Green

Write-Host "Done." -ForegroundColor Green
