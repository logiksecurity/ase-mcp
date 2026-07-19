# ase-mcp-setup.ps1
# One-shot setup for the ase-mcp portproxy bridge (HTTP MCP on the host,
# reached from a Claude Code VM). Same portproxy convention as the other DCC bridges.
# Run as Administrator on the HOST machine.

$Port     = 8001
$RuleName = "ase MCP"

Write-Host ""
Write-Host "=== ase-mcp-setup ===" -ForegroundColor Cyan
Write-Host ""

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[FAIL] Please run this script as Administrator." -ForegroundColor Red
    exit 1
}

function Test-IPv4Address {
    param([string]$Value)
    if ($Value -notmatch '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$') { return $false }
    foreach ($octet in $Matches[1..4]) {
        if ([int]$octet -gt 255) { return $false }
    }
    return $true
}

# Detect LAN IP (exclude virtual/unstable adapters).
$candidates = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike "127.*"      -and
        $_.IPAddress -notlike "169.254.*"  -and
        $_.InterfaceAlias -notmatch "VMware|VirtualBox|Hyper-V|WAN|Bluetooth|Loopback|Docker|Tailscale" -and
        $_.PrefixOrigin -ne "WellKnown"
    } |
    Sort-Object InterfaceAlias

if (-not $candidates) {
    Write-Host "[FAIL] No suitable LAN IP found." -ForegroundColor Red
    exit 1
}

if ($candidates.Count -eq 1) {
    $HostIP = $candidates[0].IPAddress
    Write-Host "[AUTO] Detected: $HostIP  ($($candidates[0].InterfaceAlias))" -ForegroundColor Green
} else {
    Write-Host "Multiple network adapters found:" -ForegroundColor Yellow
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        Write-Host "  [$i] $($candidates[$i].IPAddress)  --  $($candidates[$i].InterfaceAlias)"
    }
    $idx = Read-Host "`nSelect adapter index"
    if ($idx -notin 0..($candidates.Count - 1)) {
        Write-Host "[FAIL] Invalid selection." -ForegroundColor Red
        exit 1
    }
    $HostIP = $candidates[$idx].IPAddress
}

Write-Host ""
$confirm = Read-Host "Use $HostIP ? (Y/n)"
if ($confirm -match '^[Nn]') {
    $HostIP = Read-Host "Enter host IP manually"
    if (-not (Test-IPv4Address $HostIP)) {
        Write-Host "[FAIL] Invalid IPv4 address." -ForegroundColor Red
        exit 1
    }
}

# Allowed source scope for the firewall rule. Character guard blocks shell
# metacharacters to prevent netsh injection; netsh validates the semantics.
Write-Host ""
Write-Host "Allowed source(s) for the firewall rule:"
Write-Host "  single IP  : 192.168.1.50"
Write-Host "  range      : 192.168.1.10-192.168.1.50"
Write-Host "  subnet     : 192.168.1.0/24"
Write-Host "  list       : 192.168.1.10,192.168.1.20"
$AllowedScope = Read-Host "Enter allowed source(s) (REQUIRED - who may reach run_lua)"

# SEC B-5: an unscoped rule exposes run_lua (arbitrary command execution on this
# host) to every machine on the LAN. Refuse instead of warning and proceeding.
if (-not $AllowedScope) {
    Write-Host "[FAIL] A source scope is REQUIRED." -ForegroundColor Red
    Write-Host "       run_lua = arbitrary command execution on this host; refusing" -ForegroundColor Red
    Write-Host "       to open port $Port to the whole LAN. Re-run with the VM's IP." -ForegroundColor Yellow
    exit 1
}
if ($AllowedScope -notmatch '^[0-9./,\-]+$') {
    Write-Host "[FAIL] Invalid characters. Use digits, dots, slashes, hyphens and commas only." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Cleaning up old rules..."
netsh interface portproxy delete v4tov4 listenaddress=$HostIP listenport=$Port 2>$null | Out-Null
netsh advfirewall firewall delete rule name="$RuleName" 2>$null | Out-Null

Write-Host "[1/2] Adding portproxy rule ($HostIP`:$Port -> 127.0.0.1:$Port)..."
netsh interface portproxy add v4tov4 `
    listenaddress=$HostIP listenport=$Port `
    connectaddress=127.0.0.1 connectport=$Port
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Failed to add portproxy rule." -ForegroundColor Red
    exit 1
}
Write-Host "      OK" -ForegroundColor Green

Write-Host "[2/2] Adding firewall rule (scope: $AllowedScope)..."
netsh advfirewall firewall add rule name="$RuleName" `
    dir=in action=allow protocol=TCP localport=$Port remoteip=$AllowedScope
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Failed to add firewall rule." -ForegroundColor Red
    exit 1
}
Write-Host "      OK" -ForegroundColor Green

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$proxyOk  = netsh interface portproxy show v4tov4 | Select-String -SimpleMatch -Quiet $HostIP
$fwOk     = [bool](Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq $true })
$serverOk = Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -WarningAction SilentlyContinue

if ($proxyOk)                    { Write-Host "[OK]   Portproxy rule active." -ForegroundColor Green }
else                             { Write-Host "[FAIL] Portproxy rule not found." -ForegroundColor Red }

if ($fwOk)                       { Write-Host "[OK]   Firewall rule active." -ForegroundColor Green }
else                             { Write-Host "[FAIL] Firewall rule issue." -ForegroundColor Red }

if ($serverOk.TcpTestSucceeded)  { Write-Host "[OK]   ase-mcp reachable on localhost:$Port" -ForegroundColor Green }
else                             { Write-Host "[WARN] ase-mcp not responding on localhost:$Port -- run: python server.py" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green

# --- token + VM config -------------------------------------------------------
# The token is generated HERE if missing (server.py reads the same file), so a
# first-time user never has to start the server, find %APPDATA%, or copy a
# secret by hand. The full token is never printed (SEC B-7): it goes straight
# into the repo-root .mcp.json (gitignored), which the VM's Claude Code reads
# through the shared folder.

$tokenDir  = Join-Path $env:APPDATA "ase-mcp"
$tokenPath = Join-Path $tokenDir "token"
if (Test-Path $tokenPath) {
    $token = (Get-Content $tokenPath -Raw).Trim()
    Write-Host "[OK]   Existing token reused ($tokenPath)" -ForegroundColor Green
} else {
    New-Item -ItemType Directory -Force -Path $tokenDir | Out-Null
    $rngBytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($rngBytes)
    $token = -join ($rngBytes | ForEach-Object { $_.ToString("x2") })
    Set-Content -Path $tokenPath -Value $token -Encoding Ascii -NoNewline
    Write-Host "[OK]   Token generated ($tokenPath)" -ForegroundColor Green
}
$sha256 = [System.Security.Cryptography.SHA256]::Create()
$fingerprint = (($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($token)) |
    ForEach-Object { $_.ToString("x2") }) -join "").Substring(0, 8)

$repoRoot = Split-Path -Parent $PSScriptRoot
$mcpPath  = Join-Path $repoRoot ".mcp.json"
$aseEntry = [pscustomobject]@{
    type    = "http"
    url     = "http://${HostIP}:${Port}/mcp"
    headers = [pscustomobject]@{ Authorization = "Bearer $token" }
}
$writePath = $mcpPath
if (Test-Path $mcpPath) {
    try {
        $mcpConfig = Get-Content $mcpPath -Raw | ConvertFrom-Json -ErrorAction Stop
        if (-not $mcpConfig.PSObject.Properties["mcpServers"]) {
            $mcpConfig | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
        }
        # Replace only the ase-mcp entry; other bridges in the file are untouched.
        if ($mcpConfig.mcpServers.PSObject.Properties["ase-mcp"]) {
            $mcpConfig.mcpServers.PSObject.Properties.Remove("ase-mcp")
        }
        $mcpConfig.mcpServers | Add-Member -NotePropertyName "ase-mcp" -NotePropertyValue $aseEntry
    } catch {
        $writePath = "$mcpPath.new"
        $mcpConfig = [pscustomobject]@{ mcpServers = [pscustomobject]@{ "ase-mcp" = $aseEntry } }
        Write-Host "[WARN] Existing .mcp.json is not valid JSON; writing $writePath instead." -ForegroundColor Yellow
    }
} else {
    $mcpConfig = [pscustomobject]@{ mcpServers = [pscustomobject]@{ "ase-mcp" = $aseEntry } }
}
$mcpConfig | ConvertTo-Json -Depth 8 | Set-Content -Path $writePath -Encoding utf8
Write-Host "[OK]   VM config written: $writePath (token fingerprint $fingerprint)" -ForegroundColor Green
Write-Host ""
Write-Host "Open this folder from the VM (shared folder) and restart Claude Code."
Write-Host "If your VM project lives elsewhere, copy .mcp.json into that project."
Write-Host ""
