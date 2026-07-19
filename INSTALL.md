# ase-mcp - Installation guide

Connects an AI agent (any MCP client, e.g. Claude Code) in a VM to a live Aseprite session on the host.

```
MCP agent (VM) --http :8001--> ase-mcp server (HOST) --WS :8767--> ase-mcp plugin (in Aseprite, HOST)
```

Two machines are involved:
- HOST: runs Aseprite + the ase-mcp server + the plugin. Windows.
- VM: runs the MCP agent (e.g. Claude Code). Only needs one line in mcp.json.

---

## Part A - HOST setup

### A1. Install the extension - the server starts itself

In Aseprite: `Edit > Preferences > Extensions > Add Extension` ->
`ase-mcp-bridge.aseprite-extension` -> OK.

The extension BUNDLES the server. On load, the plugin launches it and connects.
The first time, Aseprite asks permission to run a command / use the network -
click Allow (or grant full trust in `Edit > Preferences > Scripts`). After that it
is fully automatic on every Aseprite start. No file to touch, nothing to run.

The server listens on `127.0.0.1:8001` (MCP over HTTP) and `127.0.0.1:8767`
(WebSocket for the plugin). Both bind to localhost; the VM reaches the MCP via the
portproxy in A3.

Power-user / dev alternatives: run `ase-mcp-server.exe` directly, or from source
`pip install -r requirements.txt` then `python server.py`.

### A3. Portproxy + firewall (exposes :8001 to the VM)

Open PowerShell **as Administrator**:
```
cd ase-mcp
.\scripts\ase-mcp-setup.ps1
```
It auto-detects your LAN IP and asks for the allowed source. Enter the **VM's IP**
(scopes the firewall to just the VM). It prints the exact `mcp.json` line and the
HostIP - note that IP for Part B.

To undo later: `.\scripts\ase-mcp-remove.ps1` (as Administrator).

### A4. Install the Aseprite plugin

1. Open Aseprite.
2. `Edit > Preferences > Extensions > Add Extension`.
3. Pick `ase-mcp/ase-mcp-bridge.aseprite-extension`.
4. Click OK. When Aseprite asks for **network permission** for the extension,
   allow it (or set full trust in `Edit > Preferences > Scripts`).

The plugin auto-connects to the server on Aseprite start. If you started Aseprite
before the server (A2), reconnect with `File > Scripts > ase-mcp: Connect`
(or restart Aseprite).

Order that avoids a failed first connect: A2 (server up) -> A4 (open Aseprite).

### A5. Confirm the plugin loaded (how to know it is running)

Three ways, most direct last:
1. `Edit > Preferences > Extensions` - "ase-mcp bridge" appears in the list. If it is
   not there, it did not install (re-add the `.aseprite-extension` file).
2. Aseprite Console - the plugin prints `[ase-mcp] bridge plugin loaded` then
   `[ase-mcp] connected to ws://127.0.0.1:8767` on load. Open the console from the
   `View` menu (or it pops up on any script output/error).
3. The reliable check, independent of the Aseprite UI - on the HOST run:
   ```
   .\scripts\ase-mcp-test.ps1
   ```
   It queries the server's /health endpoint and reports "Aseprite plugin connected".
   That is how you know the MCP is running even if no menu shows up.

---

## Part B - VM setup (Claude Code)

### B1. mcp.json (written for you)

`ase-mcp-setup.ps1` (A3) generates the auth token and writes a ready-to-use
`.mcp.json` at the repo root. If the VM opens this folder through the shared
folder, there is nothing to edit by hand.

If your VM project lives elsewhere, copy that `.mcp.json` into the project, or
start from `.mcp.json.example` and fill in the two placeholders (HostIP printed
by A3; token in `%APPDATA%\ase-mcp\token` on the HOST):
```json
{
  "mcpServers": {
    "ase-mcp": {
      "type": "http",
      "url": "http://HOST_LAN_IP:8001/mcp",
      "headers": { "Authorization": "Bearer PASTE_TOKEN_HERE" }
    }
  }
}
```
The token is a host-RCE credential: `.mcp.json` is gitignored, keep it that way.

### B2. Restart Claude Code

MCP servers connect at session start, so restart Claude Code after editing mcp.json.

---

## Part C - Verify

Host-side quick check first (before touching the VM), on the HOST:
```
.\scripts\ase-mcp-status.ps1    # portproxy, firewall, ports (admin)
.\scripts\ase-mcp-test.ps1      # live: server up + Aseprite plugin connected
```

Then, from Claude Code (VM), call the tool:
```
ase-mcp: aseprite_status()
```
- "sprite ..." or "connected, no active sprite" -> everything works.
- "NOT connected" -> the plugin is not talking to the server. Check: Aseprite is
  open (A4), the server is running (A2), and Aseprite granted network permission.

Quick end-to-end test:
```
new_sprite(32, 32, "rgb")
draw_rect(8, 10, 16, 14, "#8a5a2b", true)
save_png("Z:/sharedfolder/.../chest.png")
```
The PNG lands in the shared folder, readable from the VM.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `aseprite_status` says NOT connected | Server not running (A2), or Aseprite closed, or network permission denied. Use `File > Scripts > ase-mcp: Connect`. |
| Claude Code cannot reach the server | Portproxy/firewall not set (A3), wrong HostIP in mcp.json, or firewall scope excludes the VM. Re-run `ase-mcp-setup.ps1`. |
| Extension has no network access | `Edit > Preferences > Scripts` -> enable trust for the extension, restart Aseprite. |
| Tool errors on a Lua call | Aseprite Lua API differs slightly by version; adjust the snippet in `server.py` (typed tools) or the plugin. |
| Need to see the bridge traffic | In Aseprite: `File > Scripts > ase-mcp: Enable debug log` (commands + replies in the Aseprite console). From the agent: `enable_debug_log()` (server console). |

---

## What each file is

| File | Where it runs | Purpose |
|------|---------------|---------|
| `ase-mcp-server.exe` | HOST | standalone server - run this (no Python needed) |
| `server.py` | HOST | server source (FastMCP HTTP + WebSocket + tools) |
| `build_exe.bat` | build machine | rebuild the exe (needs Python + pyinstaller) |
| `ase-mcp-bridge.aseprite-extension` | HOST (in Aseprite) | the plugin (install this) |
| `aseprite-plugin/` | HOST (in Aseprite) | plugin source (package.json + ase_bridge.lua) |
| `scripts/ase-mcp-setup.ps1` | HOST (admin) | portproxy + firewall |
| `scripts/ase-mcp-remove.ps1` | HOST (admin) | tear down portproxy + firewall |
| `scripts/ase-mcp-status.ps1` | HOST (admin) | show portproxy + firewall + ports |
| `scripts/ase-mcp-test.ps1` | HOST | live check: server up + plugin connected |
| mcp.json entry | VM | points Claude Code at the host server |
