# ase-mcp

**Let an AI agent drive a live Aseprite session** over the Model Context Protocol.
Draw, animate, tag, and export inside a real, editable `.aseprite` file, from an
agent running in a VM or on the same machine.

![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)
![Aseprite 1.3+](https://img.shields.io/badge/Aseprite-1.3%2B-7d5fff.svg)
![Windows host](https://img.shields.io/badge/host-Windows-0078d6.svg)

https://github.com/user-attachments/assets/f8e563fb-5719-4ed4-853f-e88546b9fa43

> One prompt builds a full scene, house, garden, and a 12-frame baby walk-cycle,
> live in Aseprite, in a single Lua transaction. Not a flattened PNG: a real
> layered, tagged, animated `.aseprite` file you can keep editing.

Unlike a batch or headless bridge, ase-mcp acts on the session you already have
open, so what the agent does appears on your canvas as it happens.

## Install (user)

One file to install: `ase-mcp-bridge.aseprite-extension` (it bundles the plugin
AND the server).

1. Aseprite: `Edit > Preferences > Extensions > Add Extension` -> pick
   `ase-mcp-bridge.aseprite-extension` -> OK.
2. The first time, Aseprite asks permission to run a command / use the network ->
   Allow (or grant full trust in `Edit > Preferences > Scripts`).

That's it. On load the plugin launches the bundled server and connects; every later
Aseprite start is automatic.

Agent side (any MCP client; the auth token is required even on the same machine):
- VM: run `scripts/ase-mcp-setup.ps1` as admin on the HOST. It does the whole
  wiring (portproxy, scoped firewall, token generation) and writes a ready-to-use
  `.mcp.json` at the repo root. Details in `INSTALL.md`.
- Same machine: copy `.mcp.json.example`, set the url host to `127.0.0.1` and paste
  the token from `%APPDATA%/ase-mcp/token` (generated at first server start).

## Architecture

```
MCP agent (VM)   --HTTP :8001-->  ase-mcp server (HOST)  --WS :8767-->  Aseprite plugin (HOST)
                  (portproxy)      launched by the plugin                dials OUT (WS client)
```

The server binds `127.0.0.1` (8001 HTTP for the MCP, 8767 WebSocket for the
plugin). For a VM, a portproxy and scoped firewall (`scripts/ase-mcp-setup.ps1`)
expose 8001. Aseprite's Lua is a WebSocket CLIENT only, so the plugin dials OUT;
the server cannot live inside Aseprite, so the extension bundles it and
auto-launches it (`os.execute`).

## Layout

```
ase-mcp/
  ase-mcp-bridge.aseprite-extension  the deliverable (plugin + bundled exe)
  ase-mcp-server.exe                 standalone server (PyInstaller onefile)
  server.py                          server source (FastMCP HTTP + WebSocket + tools)
  build_exe.bat                      rebuild the exe (needs Python + pyinstaller)
  aseprite-plugin/                   extension source (package.json + ase_bridge.lua + exe)
  scripts/                           ase-mcp-setup / remove / status / test
  build_package.py  requirements.txt  INSTALL.md  SECURITY.md
```

## Tools

33 tools. Every tool that modifies the sprite ends its Lua with `app.refresh()`,
so the canvas repaints immediately (no need to move the mouse over Aseprite).

- `aseprite_status()` - bridge connected? active sprite info.
- `run_lua(code)` - run arbitrary Aseprite Lua in the live session (power tool).
- `new_sprite(w, h, mode)`, `save_png(path)`, `save_aseprite(path)`.
- Drawing: `draw_pixel`, `draw_pixels` (batch: [x, y] pairs, one color, one undo
  step, max 4096), `draw_rect`, `draw_line`, `draw_ellipse`, `bucket_fill`,
  `get_pixel(x, y, frame, layer)` (reads back hex/gray/index).
- Layers and cels: `add_layer`, `duplicate_layer`, `get_cels(layer)`,
  `copy_cel(from_frame, to_frame, layer, to_layer)`, `move_cel(...)` (same
  signature), `delete_cel(frame, layer)`.
- Frames: `add_frame`, `insert_frame(frame)`, `duplicate_frame(frame)`,
  `delete_frame(frame)`, `set_active_frame(frame)`,
  `set_frame_duration(frame, duration_ms)`, `set_frame_durations(durations_ms)`
  (batch, frame 1..N).
- Tags: `create_tag(name, from_frame, to_frame, direction)` (forward, reverse,
  ping_pong, ping_pong_reverse), `update_tag(name, new_name, direction)`,
  `delete_tag(name)`.
- Palette and info: `set_palette(colors)`, `sprite_info()` (size, frame
  durations in ms, layers, tags).
- Export: `export_spritesheet(path, data_path, sheet_type, border_padding,
  shape_padding)` (sheet_type: horizontal, vertical, rows, columns, packed;
  data_path writes a JSON hash data file).
- Debug: `enable_debug_log()` / `disable_debug_log()`.

Save and export paths are gated by `ASE_OUTPUT_ROOT` (see SECURITY.md B-2):
the extension must match the tool, and when the variable is set the resolved path
must stay inside that root.

## Debug log

Two independent toggles, one per side of the bridge:

- Host side (Aseprite): `File > Scripts > ase-mcp: Enable debug log` prints
  every incoming command and its reply in the Aseprite console.
  `ase-mcp: Disable debug log` turns it off.
- Agent side (MCP tools): `enable_debug_log()` / `disable_debug_log()` print
  each dispatched action, its params, and the Aseprite reply on the ase-mcp
  server console.

## Security

`run_lua` is arbitrary code execution inside Aseprite, and installing the
extension launches a bundled executable on the host (Aseprite prompts once). The
bridge is built around that: a shared token is required on both transports
(constant-time compare), save and export paths are confined by `ASE_OUTPUT_ROOT`,
the server binds `127.0.0.1`, and a VM reaches it only through the scoped
portproxy and firewall. The full threat model and a per-finding audit are in
`SECURITY.md`. Read it before exposing the port beyond a single machine.

## Status

Working, tested end to end: an AI agent running in a VM drives a live Aseprite
session on the Windows host, generating and animating a full scene (see the
video above). Targets Aseprite 1.3+; some Lua calls may need minor tweaks on
other Aseprite versions, open an issue if you hit one.

## License

 Apache-2.0. See LICENSE`.`
