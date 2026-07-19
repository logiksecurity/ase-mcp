# Security

This server drives a LIVE Aseprite session on the host. Anything that can reach
its endpoints and present the token can run code on the host. This document is
the threat model, the controls in place, and the risks accepted by design.

## Threat model

`run_lua` compiles the caller's string with Lua `load()` and runs it in the
plugin's global environment (`aseprite-plugin/ase_bridge.lua`). That environment
includes `os`, and the plugin itself calls `os.execute`. So `run_lua` is not
limited to drawing: it is arbitrary command execution on the host, under the
Aseprite process. Installing the extension also means Aseprite launches a bundled
executable (Aseprite prompts once; granting full trust makes later runs silent).

The bridge treats the token as the single credential between a reachable port and
host code execution, and is built around that assumption.

## Controls in place

- Shared bearer token required on BOTH transports (HTTP `Authorization: Bearer`
  and a WebSocket auth handshake as the first message), generated per install in
  `%APPDATA%/ase-mcp/token`. Compared in constant time (`secrets.compare_digest`)
  on both paths, so the secret is not a timing oracle.
- The server binds `127.0.0.1`. A VM reaches it only through an explicitly scoped
  portproxy and firewall rule; the setup script refuses to create an unscoped
  (whole-LAN) rule.
- Save and export paths are confined to `ASE_OUTPUT_ROOT`: the resolved path must
  stay inside that root, and the file extension must match the tool, before
  anything reaches Lua. No arbitrary file write outside the allowed root.
- The bridge pins the FIRST authenticated client for its lifetime, so a second
  local process cannot silently take over the bridge and feed forged results.
- In-flight commands are bounded, so a token holder cannot exhaust memory or
  drown the Aseprite session with unbounded pending calls.
- Startup prints only a short SHA-256 fingerprint of the token, never the token
  itself (protects against console and log capture).

## Accepted risks (by design)

- DNS-rebinding protection is OFF by default (it rejected legitimate VM requests
  with HTTP 421). Set `ASE_ALLOWED_HOSTS` to the host LAN IP to turn it back on
  with an explicit allowlist. The token is the primary control; the scoped
  firewall is the second layer.
- `/health` is unauthenticated and reports only liveness. It is a small oracle for
  a scanner that has already reached the port.
- The plugin stops a stale server by image name (`taskkill /IM`); on a machine
  deliberately running multiple instances, this can stop another instance.

`run_lua` stays: it is the power tool and the reason the bridge exists. The
mitigation is the access control around it, not its removal.

## Binaries and trust

The compiled artifacts are NOT tracked in the repo: installing them means trusting
them. Rebuild from source with `build_exe.bat` / `build_package.py` and compare
the SHA-256:

| Artifact | Bytes | SHA256 |
|---|---|---|
| `ase-mcp-server.exe` | 17933443 | `fb13e9ae9ffe1175f29eb248e0687aa6274571276fdb99b63b225a7f02b9b144` |
| `ase-mcp-bridge.aseprite-extension` | 17651620 | `103a3ee1c1d1bab31d39514d39247be5314f7dd57cd26cc99b3020de91ac96f6` |

Built 2026-07-17 from the source in this repo.

## Reporting

Found a security issue? Open a private security advisory on this repository, or
contact the maintainer, before public disclosure.
