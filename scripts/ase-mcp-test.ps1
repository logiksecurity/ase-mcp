# ase-mcp-test.ps1
# Live check: is the ase-mcp server up, and is the Aseprite plugin connected?
# No admin needed. Uses -Target (not -Host, which is reserved in PowerShell).
#
#   .\scripts\ase-mcp-test.ps1                 # local host check (127.0.0.1)
#   .\scripts\ase-mcp-test.ps1 -Target <HOST_LAN_IP>     # through the portproxy

param([string]$Target = "127.0.0.1")

$Port = 8001
$url  = "http://${Target}:${Port}/health"

Write-Host ""
Write-Host "Testing $url ..." -ForegroundColor Cyan

try {
    $r = Invoke-RestMethod -Uri $url -TimeoutSec 5 -ErrorAction Stop
    Write-Host "[OK]   Server reachable (server=$($r.server))" -ForegroundColor Green
    if ($r.aseprite_plugin) {
        Write-Host "[OK]   Aseprite plugin connected." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Server up but the Aseprite plugin is NOT connected." -ForegroundColor Yellow
        Write-Host "       Open Aseprite with the ase-mcp extension, or run" -ForegroundColor Yellow
        Write-Host "       File > Scripts > 'ase-mcp: Connect'." -ForegroundColor Yellow
    }
} catch {
    Write-Host "[FAIL] Cannot reach $url" -ForegroundColor Red
    Write-Host "       - server.py running on the host? (python server.py)" -ForegroundColor Yellow
    Write-Host "       - for a remote -Target, is the portproxy set? (ase-mcp-setup.ps1)" -ForegroundColor Yellow
}
Write-Host ""
