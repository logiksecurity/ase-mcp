"""
ase-mcp - MCP server bridging an AI agent (Claude Code, in a VM or not) to a
LIVE Aseprite session on the host.

Topology (server on the host, the agent connects to it over HTTP):

    Claude Code (VM) --HTTP MCP :8001--> ase-mcp (host)
                                              |
                              WebSocket server :8767 (localhost)
                                              ^  Aseprite dials OUT (its Lua has a
                                              |  WebSocket CLIENT only, no server)
                              Aseprite plugin (aseprite-plugin/, a WS client)

The MCP HTTP endpoint binds 127.0.0.1; a host portproxy + scoped firewall
exposes it to the VM (same convention as the other DCC MCPs). The WebSocket also
binds 127.0.0.1 - Aseprite runs on the same host, so it needs no portproxy.
"""

import asyncio
import hashlib
import json
import os
import secrets

import uvicorn
import websockets
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

MCP_HOST = os.getenv("ASE_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("ASE_MCP_PORT", "8001"))
WS_HOST = os.getenv("ASE_WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("ASE_WS_PORT", "8767"))
CMD_TIMEOUT = float(os.getenv("ASE_CMD_TIMEOUT", "30"))

# Runtime-toggled by enable_debug_log / disable_debug_log. When True, Bridge.call
# prints every dispatched action, its params, and the Aseprite reply to the
# server console.
DEBUG_LOG = False


def _load_token() -> str:
    """Shared secret for HTTP + WebSocket auth. ASE_TOKEN overrides; otherwise a
    per-install token is generated once and stored in %APPDATA%/ase-mcp/token
    (the plugin reads the same file)."""
    override = os.getenv("ASE_TOKEN")
    if override:
        return override.strip()
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    directory = os.path.join(base, "ase-mcp")
    path = os.path.join(directory, "token")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read().strip()
        if existing:
            return existing
    except OSError:
        pass
    os.makedirs(directory, exist_ok=True)
    token = secrets.token_hex(16)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


TOKEN = _load_token()


class _TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject any request without the shared token, except the open /health probe."""

    async def dispatch(self, request, call_next):
        if request.url.path.rstrip("/") != "/health":
            # SEC B-4: constant-time compare - '!=' on the secret is a timing oracle.
            provided = request.headers.get("authorization", "")
            if not secrets.compare_digest(provided, f"Bearer {TOKEN}"):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


class Bridge:
    """Holds the single connected Aseprite client and correlates requests."""

    def __init__(self):
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self._counter = 0

    def connected(self) -> bool:
        return self.ws is not None

    async def call(self, action: str, params: dict | None = None) -> dict:
        if self.ws is None:
            raise RuntimeError(
                "Aseprite bridge not connected. On the host: open Aseprite and run "
                "Install the aseprite-plugin extension in Aseprite and keep Aseprite open."
            )
        # SEC B-9: bound in-flight commands (a token holder cannot exhaust memory
        # or drown the Aseprite session with unbounded pending calls).
        if len(self.pending) >= 64:
            raise RuntimeError("too many in-flight commands; retry shortly")
        self._counter += 1
        cid = str(self._counter)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[cid] = fut
        try:
            if DEBUG_LOG:
                print("[ase-mcp debug] #%s %s %s" % (cid, action, json.dumps(params or {})[:500]), flush=True)
            await self.ws.send(json.dumps({"id": cid, "action": action, "params": params or {}}))
            resp = await asyncio.wait_for(fut, CMD_TIMEOUT)
            if DEBUG_LOG:
                print("[ase-mcp debug] #%s -> %s" % (cid, json.dumps(resp)[:500]), flush=True)
            return resp
        finally:
            self.pending.pop(cid, None)


bridge = Bridge()


async def _ws_handler(ws):
    # The first message must authenticate with the shared token.
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=10)
        auth = json.loads(first)
        # SEC B-4: constant-time compare (same timing-oracle reason as the HTTP path).
        token_ok = secrets.compare_digest(str(auth.get("token", "")), TOKEN)
        if auth.get("action") != "auth" or not token_ok:
            await ws.close(code=1008, reason="unauthorized")
            return
    except Exception:
        try:
            await ws.close(code=1008, reason="auth failed")
        except Exception:
            pass
        return

    # SEC B-6: pin the FIRST authenticated client. Last-wins let any local process
    # holding the token file hijack the bridge slot and feed the agent forged
    # results. A real reconnect still works: the websockets keepalive drops a dead
    # peer within ~20s, and the plugin retries every 1-4s until the slot frees.
    if bridge.ws is not None:
        await ws.close(code=1013, reason="bridge already connected")
        return

    bridge.ws = ws  # authenticated; first connection pinned
    try:
        async for message in ws:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            fut = bridge.pending.get(data.get("id"))
            if fut is not None and not fut.done():
                fut.set_result(data)
    finally:
        if bridge.ws is ws:
            bridge.ws = None


# The SDK enforces a DNS-rebinding Host check that only allows localhost. Behind a
# 127.0.0.1 bind + scoped portproxy/firewall + token auth, a VM request arrives with
# Host = the LAN IP and is rejected with HTTP 421. The token is the real auth, so we
# disable the host check. Set ASE_ALLOWED_HOSTS (comma-separated) to re-enable it
# with an explicit allowlist instead.
_allowed = os.getenv("ASE_ALLOWED_HOSTS", "").strip()
if _allowed:
    _security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in _allowed.split(",") if h.strip()],
        allowed_origins=["*"],
    )
else:
    _security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP("ase-mcp", host=MCP_HOST, port=MCP_PORT, transport_security=_security)

# Plain HTTP health route for the status/test scripts (added only if supported).
if hasattr(mcp, "custom_route"):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @mcp.custom_route("/health", methods=["GET"])
    async def _health(request: Request):
        return JSONResponse({"server": "up", "aseprite_plugin": bridge.connected()})


def _result(resp: dict) -> str:
    if resp.get("ok"):
        return str(resp.get("result", "ok"))
    raise RuntimeError(str(resp.get("error", "aseprite error")))


@mcp.tool()
async def aseprite_status() -> str:
    """Report whether the Aseprite bridge is connected and describe the active sprite."""
    if not bridge.connected():
        return "NOT connected. On the host: open Aseprite with the ase-mcp extension installed."
    return _result(await bridge.call("status"))


@mcp.tool()
async def enable_debug_log() -> str:
    """Enable debug logging: every bridge action, its params, and the Aseprite
    reply are printed to the ase-mcp server console on the host."""
    global DEBUG_LOG
    DEBUG_LOG = True
    return "debug log enabled"


@mcp.tool()
async def disable_debug_log() -> str:
    """Disable debug logging."""
    global DEBUG_LOG
    DEBUG_LOG = False
    return "debug log disabled"


@mcp.tool()
async def run_lua(code: str) -> str:
    """Execute arbitrary Aseprite Lua in the LIVE session and return its result.

    This is the power tool: the full Aseprite Lua API is available (Sprite, Image,
    Cel, Layer, Palette, app.command, ...). Return a value from your snippet to get
    it back as text, e.g. `return app.activeSprite.width`.
    """
    return _result(await bridge.call("run_lua", {"code": code}))


@mcp.tool()
async def new_sprite(width: int, height: int, mode: str = "rgb") -> str:
    """Create a new sprite in the live Aseprite session and make it active.

    mode: "rgb", "indexed", or "gray".
    """
    return _result(await bridge.call("new_sprite", {"width": width, "height": height, "mode": mode}))


OUTPUT_ROOT = os.getenv("ASE_OUTPUT_ROOT", "").strip()


def _guard_output_path(path: str, expected_ext: str) -> str:
    """SEC B-2: constrain where a save tool may write.

    Always enforced: the extension must match the tool, so a save tool can never
    drop a .lua/.bat/.exe over an Aseprite extension or a startup script.
    Opt-in: when ASE_OUTPUT_ROOT is set, the resolved path must stay inside it
    (full confinement). The root is configured, not guessed - the safe root is
    deployment-specific: Aseprite writes in the HOST path space, not the VM's."""
    if not path:
        raise ValueError("path required")
    if os.path.splitext(path)[1].lower() != expected_ext:
        raise ValueError("path must end with " + expected_ext)
    if OUTPUT_ROOT:
        root = os.path.realpath(OUTPUT_ROOT)
        target = os.path.realpath(path)
        if target != root and not target.startswith(root + os.sep):
            raise ValueError("path escapes ASE_OUTPUT_ROOT")
        return target
    return path


@mcp.tool()
async def save_png(path: str) -> str:
    """Save a copy of the active sprite as a PNG at `path` (use a path under the
    shared folder so the VM can read it, e.g. Z:/.../out.png)."""
    return _result(await bridge.call("save_png", {"path": _guard_output_path(path, ".png")}))


# -------------------------------------------------------------------------
# Typed tools built on top of run_lua. Each builds an Aseprite Lua snippet and
# dispatches it through the bridge, so the plugin stays minimal and the tool
# logic lives here in Python.
# -------------------------------------------------------------------------

def _color(hexstr: str):
    h = hexstr.strip().lstrip("#")
    if len(h) == 6:
        h += "ff"
    if len(h) != 8:
        raise ValueError("color must be #RRGGBB or #RRGGBBAA: " + hexstr)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _lua_layer(name: str) -> str:
    if not name:
        return "app.activeLayer"
    return ("(function() for _, l in ipairs(app.activeSprite.layers) do "
            "if l.name == " + _lua_str(name) + " then return l end end "
            "return app.activeLayer end)()")


_HEAD = 'local spr = app.activeSprite\nif not spr then error("no active sprite") end\n'

# Aseprite repaints only on UI events (e.g. mouse move); without this the canvas
# shows stale pixels until the user touches the window. https://www.aseprite.org/api/app
_REFRESH = 'app.refresh()\n'


def _use_tool(tool: str, c, points, layer: str) -> str:
    pts = ", ".join("Point(%d,%d)" % (px, py) for px, py in points)
    return (_HEAD
            + 'app.useTool{ tool=%s, color=Color{ r=%d, g=%d, b=%d, a=%d }, points={ %s }, layer=%s, frame=app.activeFrame }\n'
            % (_lua_str(tool), c[0], c[1], c[2], c[3], pts, layer)
            + _REFRESH
            + 'return %s\n' % _lua_str("drew " + tool))


async def _lua(code: str) -> str:
    return _result(await bridge.call("run_lua", {"code": code}))


@mcp.tool()
async def add_layer(name: str) -> str:
    """Add a new named layer to the active sprite and make it active."""
    code = _HEAD + ('local l = spr:newLayer()\nl.name = %s\napp.activeLayer = l\n' % _lua_str(name)
                    ) + _REFRESH + 'return "added layer " .. l.name\n'
    return await _lua(code)


@mcp.tool()
async def add_frame() -> str:
    """Append a new empty frame to the active sprite."""
    return await _lua(_HEAD + 'spr:newEmptyFrame()\n' + _REFRESH + 'return "frames=" .. #spr.frames\n')


@mcp.tool()
async def set_palette(colors: list[str]) -> str:
    """Set the sprite palette from hex colors (#RRGGBB or #RRGGBBAA)."""
    parsed = [_color(c) for c in colors]
    body = _HEAD + ("local pal = Palette(%d)\n" % len(parsed))
    for i, c in enumerate(parsed):
        body += "pal:setColor(%d, Color{ r=%d, g=%d, b=%d, a=%d })\n" % (i, c[0], c[1], c[2], c[3])
    body += 'spr:setPalette(pal)\n' + _REFRESH + 'return "palette set (" .. #pal .. " colors)"\n'
    return await _lua(body)


@mcp.tool()
async def draw_pixel(x: int, y: int, color: str, layer: str = "") -> str:
    """Draw a single pixel at (x, y). color is hex; layer defaults to the active layer."""
    return await _lua(_use_tool("pencil", _color(color), [(x, y)], _lua_layer(layer)))


@mcp.tool()
async def draw_pixels(pixels: list[list[int]], color: str, layer: str = "") -> str:
    """Draw many pixels of one color in a single call (one undo step).

    pixels: list of [x, y] pairs, e.g. [[0, 0], [1, 2], [3, 3]]. Max 4096 per call.
    """
    if not pixels:
        raise ValueError("pixels is empty")
    if len(pixels) > 4096:
        raise ValueError("max 4096 pixels per call")
    for p in pixels:
        if len(p) != 2:
            raise ValueError("each pixel must be [x, y]")
    c = _color(color)
    pts = ", ".join("{%d,%d}" % (p[0], p[1]) for p in pixels)
    code = _HEAD + (
        'local pts = { %s }\n'
        'local col = Color{ r=%d, g=%d, b=%d, a=%d }\n'
        'local lay = %s\n'
        'app.transaction(function()\n'
        '  for _, p in ipairs(pts) do\n'
        '    app.useTool{ tool="pencil", color=col, points={ Point(p[1], p[2]) }, layer=lay, frame=app.activeFrame }\n'
        '  end\n'
        'end)\n' % (pts, c[0], c[1], c[2], c[3], _lua_layer(layer))
    ) + _REFRESH + 'return "drew " .. #pts .. " pixels"\n'
    return await _lua(code)


@mcp.tool()
async def copy_cel(from_frame: int, to_frame: int, layer: str = "", to_layer: str = "") -> str:
    """Copy the cel at `from_frame` (1-based) to `to_frame`, replacing any cel there.

    layer: source layer (default: active). to_layer: destination (default: same
    as source). The destination frame must already exist (use add_frame first).
    """
    if from_frame < 1 or to_frame < 1:
        raise ValueError("frames are 1-based")
    if from_frame == to_frame and not to_layer:
        raise ValueError("source and destination are the same cel")
    dst_expr = _lua_layer(to_layer) if to_layer else "src_layer"
    code = _HEAD + (
        'local src_layer = %s\n'
        'local dst_layer = %s\n'
        'if not spr.frames[%d] then error("no frame %d") end\n'
        'local c = spr:cel(src_layer, %d)\n'
        'if not c then error("no cel at frame %d on the source layer") end\n'
        'if spr:cel(dst_layer, %d) then spr:deleteCel(dst_layer, %d) end\n'
        'spr:newCel(dst_layer, %d, c.image:clone(), c.position)\n'
        % (_lua_layer(layer), dst_expr, to_frame, to_frame, from_frame, from_frame,
           to_frame, to_frame, to_frame)
    ) + _REFRESH + 'return ' + _lua_str("copied cel %d -> %d" % (from_frame, to_frame)) + '\n'
    return await _lua(code)


@mcp.tool()
async def move_cel(from_frame: int, to_frame: int, layer: str = "", to_layer: str = "") -> str:
    """Move the cel at `from_frame` (1-based) to `to_frame`, replacing any cel
    there and clearing the source. Defaults as in copy_cel."""
    if from_frame < 1 or to_frame < 1:
        raise ValueError("frames are 1-based")
    if from_frame == to_frame and not to_layer:
        raise ValueError("source and destination are the same cel")
    dst_expr = _lua_layer(to_layer) if to_layer else "src_layer"
    code = _HEAD + (
        'local src_layer = %s\n'
        'local dst_layer = %s\n'
        'if not spr.frames[%d] then error("no frame %d") end\n'
        'local c = spr:cel(src_layer, %d)\n'
        'if not c then error("no cel at frame %d on the source layer") end\n'
        'if spr:cel(dst_layer, %d) then spr:deleteCel(dst_layer, %d) end\n'
        'spr:newCel(dst_layer, %d, c.image:clone(), c.position)\n'
        'spr:deleteCel(src_layer, %d)\n'
        % (_lua_layer(layer), dst_expr, to_frame, to_frame, from_frame, from_frame,
           to_frame, to_frame, to_frame, from_frame)
    ) + _REFRESH + 'return ' + _lua_str("moved cel %d -> %d" % (from_frame, to_frame)) + '\n'
    return await _lua(code)


@mcp.tool()
async def get_cels(layer: str = "") -> str:
    """List cels: layer name, frame, position, and size. `layer` filters to one
    layer by name (default: all layers)."""
    if layer:
        cond = "l.name == " + _lua_str(layer)
    else:
        cond = "true"
    code = _HEAD + (
        'local out = {}\n'
        'for _, l in ipairs(spr.layers) do\n'
        '  if ' + cond + ' then\n'
        '    for _, fr in ipairs(spr.frames) do\n'
        '      local c = spr:cel(l, fr.frameNumber)\n'
        '      if c then\n'
        '        out[#out+1] = string.format("%s[%d] pos=(%d,%d) %dx%d",\n'
        '          l.name, fr.frameNumber, c.position.x, c.position.y, c.image.width, c.image.height)\n'
        '      end\n'
        '    end\n'
        '  end\n'
        'end\n'
        'if #out == 0 then return "no cels" end\n'
        'return table.concat(out, "; ")\n'
    )
    return await _lua(code)


@mcp.tool()
async def get_pixel(x: int, y: int, frame: int = 0, layer: str = "") -> str:
    """Read the pixel at sprite coordinate (x, y). frame 0 = active frame;
    layer defaults to the active layer. Returns hex + alpha for RGB sprites,
    gray value for grayscale, palette index for indexed."""
    if frame < 0:
        raise ValueError("frame must be 0 (active) or >= 1")
    code = _HEAD \
        + 'local l = %s\n' % _lua_layer(layer) \
        + 'local frnum = %d\n' % frame \
        + 'local px = %d\nlocal py = %d\n' % (x, y) + (
        'if frnum == 0 then frnum = app.activeFrame.frameNumber end\n'
        'local c = spr:cel(l, frnum)\n'
        'if not c then return "transparent (no cel)" end\n'
        'local ix = px - c.position.x\n'
        'local iy = py - c.position.y\n'
        'if ix < 0 or iy < 0 or ix >= c.image.width or iy >= c.image.height then\n'
        '  return "transparent (outside cel)"\n'
        'end\n'
        'local v = c.image:getPixel(ix, iy)\n'
        'local pc = app.pixelColor\n'
        'if spr.colorMode == ColorMode.RGB then\n'
        '  return string.format("#%02X%02X%02X a=%d", pc.rgbaR(v), pc.rgbaG(v), pc.rgbaB(v), pc.rgbaA(v))\n'
        'elseif spr.colorMode == ColorMode.GRAY then\n'
        '  return string.format("gray=%d a=%d", pc.grayaV(v), pc.grayaA(v))\n'
        'else\n'
        '  return "index=" .. v\n'
        'end\n'
    )
    return await _lua(code)


@mcp.tool()
async def draw_rect(x: int, y: int, width: int, height: int, color: str, fill: bool = True, layer: str = "") -> str:
    """Draw a rectangle at (x, y) of the given size. fill=True solid, False outline."""
    tool = "filled_rectangle" if fill else "rectangle"
    pts = [(x, y), (x + width - 1, y + height - 1)]
    return await _lua(_use_tool(tool, _color(color), pts, _lua_layer(layer)))


@mcp.tool()
async def draw_line(x1: int, y1: int, x2: int, y2: int, color: str, layer: str = "") -> str:
    """Draw a line from (x1, y1) to (x2, y2)."""
    return await _lua(_use_tool("line", _color(color), [(x1, y1), (x2, y2)], _lua_layer(layer)))


@mcp.tool()
async def draw_ellipse(x: int, y: int, width: int, height: int, color: str, fill: bool = True, layer: str = "") -> str:
    """Draw an ellipse in the bounding box (x, y, width, height)."""
    tool = "filled_ellipse" if fill else "ellipse"
    pts = [(x, y), (x + width - 1, y + height - 1)]
    return await _lua(_use_tool(tool, _color(color), pts, _lua_layer(layer)))


@mcp.tool()
async def bucket_fill(x: int, y: int, color: str, layer: str = "") -> str:
    """Flood-fill (paint bucket) the contiguous region starting at (x, y)."""
    return await _lua(_use_tool("paint_bucket", _color(color), [(x, y)], _lua_layer(layer)))


@mcp.tool()
async def duplicate_layer(new_name: str = "", source_layer: str = "") -> str:
    """Duplicate a layer with all its cels (the active one, or `source_layer`) and
    make the copy active. Useful for building variants."""
    src = _lua_layer(source_layer)
    nm = _lua_str(new_name) if new_name else '(src.name .. " copy")'
    body = _HEAD + (
        "local src = %s\n"
        "local dst = spr:newLayer()\n"
        "dst.name = %s\n"
        "for _, fr in ipairs(spr.frames) do\n"
        "  local c = spr:cel(src, fr)\n"
        "  if c then spr:newCel(dst, fr, c.image:clone(), c.position) end\n"
        "end\n"
        "app.activeLayer = dst\n"
        + _REFRESH +
        'return "duplicated to " .. dst.name\n'
    ) % (src, nm)
    return await _lua(body)


# Tool-facing strings -> Aseprite Lua enum names (validated at the MCP boundary
# so bad input fails in Python, before any Lua is generated).
_ANI_DIRS = {
    "forward": "AniDir.FORWARD",
    "reverse": "AniDir.REVERSE",
    "ping_pong": "AniDir.PING_PONG",
    "ping_pong_reverse": "AniDir.PING_PONG_REVERSE",
}

_SHEET_TYPES = {
    "horizontal": "SpriteSheetType.HORIZONTAL",
    "vertical": "SpriteSheetType.VERTICAL",
    "rows": "SpriteSheetType.ROWS",
    "columns": "SpriteSheetType.COLUMNS",
    "packed": "SpriteSheetType.PACKED",
}


@mcp.tool()
async def sprite_info() -> str:
    """Describe the active sprite: size, frame count with durations in ms,
    layer names, and tags with their frame ranges."""
    code = _HEAD + (
        'local durs = {}\n'
        'for _, fr in ipairs(spr.frames) do durs[#durs+1] = math.floor(fr.duration * 1000 + 0.5) end\n'
        'local layers = {}\n'
        'for _, l in ipairs(spr.layers) do layers[#layers+1] = l.name end\n'
        'local tags = {}\n'
        'for _, t in ipairs(spr.tags) do\n'
        '  tags[#tags+1] = string.format("%s[%d..%d]", t.name, t.fromFrame.frameNumber, t.toFrame.frameNumber)\n'
        'end\n'
        'return string.format("%dx%d frames=%d durations_ms=[%s] layers=[%s] tags=[%s]",\n'
        '  spr.width, spr.height, #spr.frames,\n'
        '  table.concat(durs, ","), table.concat(layers, ","), table.concat(tags, ","))\n'
    )
    return await _lua(code)


@mcp.tool()
async def set_frame_duration(frame: int, duration_ms: int) -> str:
    """Set the duration of frame `frame` (1-based) in milliseconds."""
    if frame < 1 or duration_ms < 1:
        raise ValueError("frame and duration_ms must be >= 1")
    code = _HEAD + (
        'local fr = spr.frames[%d]\n'
        'if not fr then error("no frame %d") end\n'
        'fr.duration = %s\n' % (frame, frame, repr(duration_ms / 1000.0))
    ) + _REFRESH + 'return ' + _lua_str("frame %d = %d ms" % (frame, duration_ms)) + '\n'
    return await _lua(code)


@mcp.tool()
async def set_frame_durations(durations_ms: list[int]) -> str:
    """Set durations for frames 1..N in one call. durations_ms[i] applies to
    frame i+1, in milliseconds. Max 1024 entries."""
    if not durations_ms:
        raise ValueError("durations_ms is empty")
    if len(durations_ms) > 1024:
        raise ValueError("max 1024 entries")
    if any(d < 1 for d in durations_ms):
        raise ValueError("every duration must be >= 1 ms")
    table = ", ".join(str(d) for d in durations_ms)
    code = _HEAD + (
        'local ms = { %s }\n'
        'for i, m in ipairs(ms) do\n'
        '  local fr = spr.frames[i]\n'
        '  if not fr then error("no frame " .. i) end\n'
        '  fr.duration = m / 1000\n'
        'end\n' % table
    ) + _REFRESH + 'return "set " .. #ms .. " frame durations"\n'
    return await _lua(code)


@mcp.tool()
async def insert_frame(frame: int) -> str:
    """Insert a new empty frame at position `frame` (1-based); later frames shift."""
    if frame < 1:
        raise ValueError("frame must be >= 1")
    code = _HEAD + ('spr:newEmptyFrame(%d)\n' % frame) \
        + _REFRESH + 'return "frames=" .. #spr.frames\n'
    return await _lua(code)


@mcp.tool()
async def duplicate_frame(frame: int) -> str:
    """Duplicate frame `frame` (1-based) with all its cels."""
    if frame < 1:
        raise ValueError("frame must be >= 1")
    code = _HEAD + (
        'if not spr.frames[%d] then error("no frame %d") end\n'
        'spr:newFrame(%d)\n' % (frame, frame, frame)
    ) + _REFRESH + 'return "frames=" .. #spr.frames\n'
    return await _lua(code)


@mcp.tool()
async def set_active_frame(frame: int) -> str:
    """Make frame `frame` (1-based) the active frame in the Aseprite UI."""
    if frame < 1:
        raise ValueError("frame must be >= 1")
    code = _HEAD + (
        'if not spr.frames[%d] then error("no frame %d") end\n'
        'app.activeFrame = spr.frames[%d]\n' % (frame, frame, frame)
    ) + _REFRESH + 'return "active frame = %d"\n' % frame
    return await _lua(code)


@mcp.tool()
async def delete_frame(frame: int) -> str:
    """Delete frame `frame` (1-based) from the active sprite."""
    if frame < 1:
        raise ValueError("frame must be >= 1")
    code = _HEAD + (
        'local fr = spr.frames[%d]\n'
        'if not fr then error("no frame %d") end\n'
        'spr:deleteFrame(fr)\n' % (frame, frame)
    ) + _REFRESH + 'return "frames=" .. #spr.frames\n'
    return await _lua(code)


@mcp.tool()
async def create_tag(name: str, from_frame: int, to_frame: int, direction: str = "forward") -> str:
    """Create an animation tag over frames [from_frame, to_frame] (1-based).

    direction: forward, reverse, ping_pong, or ping_pong_reverse.
    """
    lua_dir = _ANI_DIRS.get(direction)
    if lua_dir is None:
        raise ValueError("direction must be one of: " + ", ".join(sorted(_ANI_DIRS)))
    if from_frame < 1 or to_frame < from_frame:
        raise ValueError("need 1 <= from_frame <= to_frame")
    code = _HEAD + (
        'local t = spr:newTag(%d, %d)\n'
        't.name = %s\n'
        't.aniDir = %s\n' % (from_frame, to_frame, _lua_str(name), lua_dir)
    ) + _REFRESH + 'return "tag " .. t.name .. " created"\n'
    return await _lua(code)


@mcp.tool()
async def delete_tag(name: str) -> str:
    """Delete the animation tag with the given name."""
    code = _HEAD + ('spr:deleteTag(%s)\n' % _lua_str(name)) \
        + _REFRESH + 'return ' + _lua_str("deleted tag " + name) + '\n'
    return await _lua(code)


@mcp.tool()
async def update_tag(name: str, new_name: str = "", direction: str = "") -> str:
    """Update the tag `name`: rename it (new_name) and/or change its playback
    direction (forward, reverse, ping_pong, ping_pong_reverse)."""
    if not new_name and not direction:
        raise ValueError("nothing to update: give new_name and/or direction")
    lua_dir = ""
    if direction:
        lua_dir = _ANI_DIRS.get(direction)
        if lua_dir is None:
            raise ValueError("direction must be one of: " + ", ".join(sorted(_ANI_DIRS)))
    updates = ""
    if new_name:
        updates += 't.name = %s\n' % _lua_str(new_name)
    if lua_dir:
        updates += 't.aniDir = %s\n' % lua_dir
    code = _HEAD + (
        'local t = nil\n'
        'for _, tg in ipairs(spr.tags) do\n'
        '  if tg.name == %s then t = tg break end\n'
        'end\n'
        'if not t then error("no tag named " .. %s) end\n'
        % (_lua_str(name), _lua_str(name))
    ) + updates + _REFRESH + 'return "tag updated: " .. t.name\n'
    return await _lua(code)


@mcp.tool()
async def delete_cel(frame: int, layer: str = "") -> str:
    """Delete the cel at frame `frame` (1-based) on `layer` (default: active layer)."""
    if frame < 1:
        raise ValueError("frame must be >= 1")
    code = _HEAD + (
        'local l = %s\n'
        'spr:deleteCel(l, %d)\n' % (_lua_layer(layer), frame)
    ) + _REFRESH + 'return "cel deleted"\n'
    return await _lua(code)


@mcp.tool()
async def export_spritesheet(path: str, data_path: str = "", sheet_type: str = "horizontal",
                             border_padding: int = 0, shape_padding: int = 0) -> str:
    """Export the active sprite as a spritesheet PNG at `path`, optionally with a
    JSON data file at `data_path` (frame metadata, JSON hash format).

    sheet_type: horizontal, vertical, rows, columns, or packed.
    """
    lua_type = _SHEET_TYPES.get(sheet_type)
    if lua_type is None:
        raise ValueError("sheet_type must be one of: " + ", ".join(sorted(_SHEET_TYPES)))
    texture = _guard_output_path(path, ".png")
    params = [
        "ui=false",
        "askOverwrite=false",
        "type=" + lua_type,
        "textureFilename=" + _lua_str(texture),
        "borderPadding=%d" % border_padding,
        "shapePadding=%d" % shape_padding,
    ]
    if data_path:
        params.append("dataFilename=" + _lua_str(_guard_output_path(data_path, ".json")))
        params.append("dataFormat=SpriteSheetDataFormat.JSON_HASH")
    # ExportSpriteSheet fails silently on an unwritable path (e.g. a VM-only
    # drive letter the host does not have), so verify the file actually landed.
    code = _HEAD + ("app.command.ExportSpriteSheet{ %s }\n" % ", ".join(params)) \
        + 'if not app.fs.isFile(%s) then\n' % _lua_str(texture) \
        + '  error("export wrote nothing: path must be writable on the HOST (Aseprite side), not a VM-only path")\n' \
        + 'end\n' \
        + 'return ' + _lua_str("exported " + texture) + '\n'
    return await _lua(code)


@mcp.tool()
async def save_aseprite(path: str) -> str:
    """Save the active sprite as a .aseprite file at `path`."""
    safe = _guard_output_path(path, ".aseprite")
    return await _lua(_HEAD + ('spr:saveAs(%s)\nreturn "saved " .. spr.filename\n' % _lua_str(safe)))


async def _serve() -> None:
    # One event loop: the WebSocket server (for the Aseprite plugin) and the MCP
    # HTTP app (for Claude Code) run together, so tool calls and WS replies share
    # the loop. Starting the WS here is reliable (the FastMCP lifespan was not).
    async with websockets.serve(_ws_handler, WS_HOST, WS_PORT):
        print(f"[ase-mcp] WebSocket up on {WS_HOST}:{WS_PORT}")
        # SEC B-7: print a fingerprint, never the secret (console + log capture).
        fingerprint = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()[:8]
        print(f"[ase-mcp] token fingerprint: {fingerprint} (full token: %APPDATA%/ase-mcp/token)")
        app = mcp.streamable_http_app()
        app.add_middleware(_TokenAuthMiddleware)
        config = uvicorn.Config(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
        await uvicorn.Server(config).serve()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
