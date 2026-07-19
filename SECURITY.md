# ase-mcp - Security audit

Audit 2026-07-14 (supersedes the 2026-07-05 audit below). This server drives a
live Aseprite session on the host: anything that can reach its endpoints and hold
the token can run code on the host.

## Threat model (what the attacker actually gets)

`run_lua` compiles the caller's string with Lua `load()` and runs it in the
plugin's global environment (`aseprite-plugin/ase_bridge.lua:65`). That
environment contains `os` - the plugin itself calls `os.execute` (lines 88, 125).
So `run_lua` is NOT limited to drawing: it is **arbitrary command execution on the
host**, under the Aseprite process. The install guide tells the user to grant
"full trust" (`INSTALL.md:24`, `:53`), which removes Aseprite's own permission
prompt, so the execution is silent.

Auth is a single shared bearer token on both transports. DNS-rebinding protection
is disabled (`server.py:150`), so **the token is the only thing standing between a
reachable port and host RCE**.

## Findings (2026-07-14)

| # | Sev | Issue |
|---|-----|-------|
| B-1 | **CRITICAL** | **The live token is committed and pushed.** `.mcp.json` at the SpriteHatch repo root carries a live `Bearer <32-hex>` token (redacted here; the value is the one in `%APPDATA%/ase-mcp/token`) and was tracked in EVERY commit of the pushed Gitea repo (`git log -S` hits all 8 commits). `ase-mcp/` is gitignored, but the root `.mcp.json` duplicates the secret. Anyone with repo read access holds the only credential that guards host RCE (see threat model). If the repo is ever made public for the launch, the token is public. **Rotating it is the fix; purging the history alone is not.** |
| B-2 | HIGH | **No path confinement on writes.** `save_png(path)` and `save_aseprite(path)` pass the caller's path straight to `saveCopyAs` / `saveAs` (`server.py:199`, `:327`). Arbitrary file write anywhere the Aseprite process can reach (overwrite a startup script, a .lua extension, the user's own .aseprite sources). Same class as the desktop `safeRelPath` bug already fixed in the SaaS. |
| B-3 | HIGH | **DNS-rebinding protection off by default** (`server.py:150`). Deliberate (it 421'd VM requests), but it means a browser on the host or VM can be made to POST to `127.0.0.1:8001`. Only the token stops it - and B-1 leaks the token. |
| B-4 | MED | **Token compared with `!=`** on both transports (`server.py:70`, `:112`): not constant-time. Byte-by-byte timing oracle over the LAN. `secrets.compare_digest` is the one-line fix. |
| B-5 | MED | **The firewall rule defaults to the whole LAN.** `scripts/ase-mcp-setup.ps1:76` accepts a blank scope and then allows every inbound host on :8001 (it warns, but proceeds). The safe default is to refuse to create an unscoped rule. |
| B-6 | MED | **Bridge slot is last-wins** (`server.py:122`). Any local process that reads the token file (plaintext in `%APPDATA%/ase-mcp/token`, user-readable) can take over the bridge, impersonate Aseprite, and feed forged results to the agent. |
| B-7 | LOW | **Token printed to the console at startup** (`server.py:338`) and echoed by the setup script. Shoulder-surfing / log capture. |
| B-8 | LOW | **`/health` is unauthenticated** (`server.py:159`) and discloses whether Aseprite is connected. Acceptable, but it is a live-host oracle for an unauthenticated scanner. |
| B-9 | LOW | **No rate limiting.** A token holder can spam Aseprite into unusability. |
| B-10 | LOW | **`taskkill /IM ase-mcp-server.exe /F`** (`ase_bridge.lua:125`) kills by image name: it will kill another user's/instance's server too. |

Carried over from 2026-07-05, still valid: the extension bundles and auto-runs an
18 MB PyInstaller binary (`ase-mcp-server.exe`); installing it = trusting that
binary. Publish a SHA256 and let people rebuild from source (`build_exe.bat`).

## Hardening plan (proposed order)

1. **B-1 first, and it needs the owner:** generate a new token, purge the old one
   from the repo history, and keep `.mcp.json` out of git (add it to `.gitignore`,
   ship a `.mcp.json.example` with a placeholder). Rotation means: delete
   `%APPDATA%/ase-mcp/token`, restart the server, paste the new token into the
   VM's `.mcp.json`, restart Claude Code.
2. **B-2:** confine `save_png` / `save_aseprite` to an allowlisted root
   (`ASE_OUTPUT_ROOT`, default the shared folder); resolve the path and reject
   anything outside it, before it reaches Lua.
3. **B-4:** `secrets.compare_digest` on both auth paths.
4. **B-5:** require an explicit scope in the setup script (no blank = no rule).
5. **B-6:** pin the first authenticated bridge client instead of last-wins.
6. **B-3:** set `ASE_ALLOWED_HOSTS` to the host's LAN IP in the documented setup,
   so the rebinding check is back on with an explicit allowlist.
7. **B-7/B-8/B-9/B-10:** print a fingerprint instead of the token, keep `/health`
   but make it say nothing but "up", add a simple per-token rate limit, kill the
   server by PID.

`run_lua` itself stays (it is the power tool and the reason the bridge exists);
the mitigation is the access control around it, not its removal. Revisit once the
typed tool set covers the real workflows.

---

## Previous audit (2026-07-05, kept for history)

Findings A-1..A-8: unauthenticated RCE via `run_lua` (A-1/A-2), unconfined
`save_png` (A-3), unauthenticated WebSocket (A-4), plaintext transport (A-5),
last-wins bridge (A-6), no rate limiting (A-7), bundled auto-run binary (A-8).

Hardening applied 2026-07-07: a shared token is required on BOTH transports (HTTP
`Authorization: Bearer`, and a WebSocket auth handshake as the first message),
generated per install in `%APPDATA%/ase-mcp/token`. That closed A-1/A-4 as
*unauthenticated* paths - which is exactly why B-1 (the token being in the pushed
repo) is now the critical finding: the whole 2026-07-07 mitigation rests on that
secret.

## Shipped artifact checksums (A-8)

The binaries are NOT in the repo (installing them means trusting them).
Rebuild from source with `build_exe.bat` / `build_package.py` and compare:

| Artifact | Bytes | SHA256 |
|---|---|---|
| `ase-mcp-server.exe` | 17933443 | `fb13e9ae9ffe1175f29eb248e0687aa6274571276fdb99b63b225a7f02b9b144` |
| `ase-mcp-bridge.aseprite-extension` | 17651620 | `103a3ee1c1d1bab31d39514d39247be5314f7dd57cd26cc99b3020de91ac96f6` |

Built 2026-07-17 from the source in this repo (typed tool set + hardened bridge).

## Not applicable

NASA Power of Ten / C++ standards do not apply here (Python + Lua, not the
SpriteHatch C++ codebase). Code is dependency-light (`mcp`, `websockets`,
`starlette`), no dead code.
