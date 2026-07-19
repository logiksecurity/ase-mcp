# ase-mcp-status.ps1
# Show the ase-mcp portproxy, firewall, and server state. Run as Administrator.

$Port     = 8001
$WsPort   = 8767
$RuleName = "ase MCP"

Write-Host ""
Write-Host "=== ase-mcp-status ===" -ForegroundColor Cyan
Write-Host ""

# Portproxy
$proxy = netsh interface portproxy show v4tov4 | Select-String -Pattern "$Port"
if ($proxy) {
    Write-Host "[OK]   Portproxy rule(s) for :$Port" -ForegroundColor Green
    $proxy | ForEach-Object { Write-Host "       $($_.ToString().Trim())" }
} else {
    Write-Host "[--]   No portproxy rule for :$Port" -ForegroundColor Yellow
}

# Firewall
$fw = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($fw) {
    $scope = ($fw | Get-NetFirewallAddressFilter).RemoteAddress -join ", "
    Write-Host "[OK]   Firewall rule '$RuleName' (enabled=$($fw.Enabled), remote=$scope)" -ForegroundColor Green
} else {
    Write-Host "[--]   No firewall rule '$RuleName'" -ForegroundColor Yellow
}

# Ports
$mcpOk = Test-NetConnection -ComputerName 127.0.0.1 -Port $Port   -WarningAction SilentlyContinue
$wsOk  = Test-NetConnection -ComputerName 127.0.0.1 -Port $WsPort -WarningAction SilentlyContinue

if ($mcpOk.TcpTestSucceeded) { Write-Host "[OK]   MCP server up on 127.0.0.1:$Port" -ForegroundColor Green }
else                         { Write-Host "[FAIL] MCP server not responding on 127.0.0.1:$Port -- run: python server.py" -ForegroundColor Red }

if ($wsOk.TcpTestSucceeded)  { Write-Host "[OK]   WebSocket up on 127.0.0.1:$WsPort" -ForegroundColor Green }
else                         { Write-Host "[FAIL] WebSocket not up on 127.0.0.1:$WsPort" -ForegroundColor Red }

Write-Host ""
Write-Host "For a live check (server + Aseprite plugin), run: .\scripts\ase-mcp-test.ps1"
Write-Host ""
