-- ase-mcp Aseprite plugin (entry point; package.json contributes.scripts -> here).
--
-- On load this launches the bundled ase-mcp-server.exe (auto-start) and connects
-- to it over WebSocket. Aseprite's Lua is a WebSocket CLIENT only, so it dials OUT.
-- init() must NEVER throw (else Aseprite drops the extension), so every step is
-- guarded. Aseprite asks for permission the first time a script runs a command /
-- uses the network: allow it (or grant full trust in Preferences > Scripts).

local EXT_NAME = "ase-mcp-bridge"          -- Aseprite unpacks the extension here
local WS_URL   = "ws://127.0.0.1:8767"
local ws = nil
-- Toggled by the File > Scripts menu commands below; when true every bridge
-- command and its reply are printed to the Aseprite console.
local DEBUG_LOG = false

local function read_token()
    local ok, tok = pcall(function()
        local appdata = os.getenv("APPDATA")
        if not appdata then return "" end
        local path = app.fs.joinPath(appdata, "ase-mcp", "token")
        if not app.fs.isFile(path) then return "" end
        local f = io.open(path, "r")
        if not f then return "" end
        local t = f:read("l") or ""
        f:close()
        return (t:gsub("%s+", ""))
    end)
    return ok and tok or ""
end

local function reply(tbl)
    if ws then ws:sendText(json.encode(tbl)) end
end

local function handle(raw)
    local ok, msg = pcall(json.decode, raw)
    if not ok or msg == nil then return end  -- not JSON (e.g. a ping frame)
    if DEBUG_LOG then print("[ase-mcp debug] <- " .. raw) end
    local resp = { id = msg.id }
    local action = msg.action
    local p = msg.params or {}

    if action == "status" then
        local s = app.activeSprite
        resp.ok = true
        if s then
            resp.result = string.format("sprite %dx%d, %d frame(s), %d layer(s), file=%s",
                s.width, s.height, #s.frames, #s.layers, tostring(s.filename))
        else
            resp.result = "connected, no active sprite"
        end
    elseif action == "new_sprite" then
        local mode = ColorMode.RGB
        if p.mode == "indexed" then mode = ColorMode.INDEXED
        elseif p.mode == "gray" then mode = ColorMode.GRAY end
        local s = Sprite(p.width or 32, p.height or 32, mode)
        app.activeSprite = s
        resp.ok = true
        resp.result = string.format("created %dx%d %s", s.width, s.height, tostring(p.mode or "rgb"))
    elseif action == "save_png" then
        local s = app.activeSprite
        if not s then resp.ok = false; resp.error = "no active sprite"
        else
            local ok2, err = pcall(function() s:saveCopyAs(p.path) end)
            resp.ok = ok2
            if ok2 then resp.result = "saved " .. tostring(p.path) else resp.error = tostring(err) end
        end
    elseif action == "run_lua" then
        local fn, cerr = load(p.code or "")
        if not fn then resp.ok = false; resp.error = "compile: " .. tostring(cerr)
        else
            local ok2, res = pcall(fn)
            if ok2 then resp.ok = true; resp.result = (res ~= nil) and tostring(res) or "ok"
            else resp.ok = false; resp.error = "runtime: " .. tostring(res) end
        end
    else
        resp.ok = false; resp.error = "unknown action: " .. tostring(action)
    end
    if DEBUG_LOG then print("[ase-mcp debug] -> " .. json.encode(resp)) end
    reply(resp)
end

local function server_exe()
    return app.fs.joinPath(app.fs.userConfigPath, "extensions", EXT_NAME, "ase-mcp-server.exe")
end

local function launch_server()
    local exe = server_exe()
    if app.fs.isFile(exe) then
        -- 'start' detaches so os.execute returns at once (the server never exits).
        -- If a server is already running, this second instance fails to bind and
        -- exits harmlessly. Aseprite prompts for command permission the first time.
        os.execute('start "" /min "' .. exe .. '"')
    end
end

local function connect()
    if WebSocket == nil then
        print("[ase-mcp] no WebSocket API in this Aseprite build (need 1.3+).")
        return
    end
    local ok, err = pcall(function()
        if ws then ws:close() end
        ws = WebSocket{
            url = WS_URL,
            deflate = false,
            minreconnectwait = 1,   -- keep retrying while the server boots
            maxreconnectwait = 4,
            onreceive = function(mt, data)
                if mt == WebSocketMessageType.OPEN then
                    ws:sendText(json.encode({ action = "auth", token = read_token() }))
                    print("[ase-mcp] connected to " .. WS_URL)
                elseif mt == WebSocketMessageType.CLOSE then
                    print("[ase-mcp] disconnected")
                    print("[ase-mcp] plugin uninstalled")
                elseif data ~= nil and #data > 0 then
                    -- any data-carrying frame is a command; enum values vary by Aseprite build
                    handle(data)
                end
            end,
        }
        ws:connect()
    end)
    if ok then print("[ase-mcp] connecting to " .. WS_URL)
    else print("[ase-mcp] connect error: " .. tostring(err)) end
end

local function disconnect()
    if ws then pcall(function() ws:close() end); ws = nil end
    -- also stop the bundled server so no console window lingers. Connect relaunches it.
    pcall(function() os.execute('taskkill /IM ase-mcp-server.exe /F >nul 2>&1') end)
end

function init(plugin)
    print("[ase-mcp] bridge plugin loaded")
    pcall(function()
        plugin:newCommand{ id = "ase_mcp_connect", title = "ase-mcp: Connect",
            group = "file_scripts", onclick = function() launch_server(); connect() end }
    end)
    pcall(function()
        plugin:newCommand{ id = "ase_mcp_disconnect", title = "ase-mcp: Disconnect",
            group = "file_scripts", onclick = disconnect }
    end)
    pcall(function()
        plugin:newCommand{ id = "ase_mcp_debug_on", title = "ase-mcp: Enable debug log",
            group = "file_scripts", onclick = function()
                DEBUG_LOG = true; print("[ase-mcp] debug log enabled") end }
    end)
    pcall(function()
        plugin:newCommand{ id = "ase_mcp_debug_off", title = "ase-mcp: Disable debug log",
            group = "file_scripts", onclick = function()
                DEBUG_LOG = false; print("[ase-mcp] debug log disabled") end }
    end)
    pcall(launch_server)   -- auto-start the bundled server
    pcall(function()
        plugin:newCommand{ id = "ase_mcp_status", title = "ase-mcp: Status",
            group = "file_scripts", onclick = function()
                local txt = (ws ~= nil) and "WS object present" or "not connected"
                local sp = app.activeSprite
                if sp then txt = txt .. string.format("; sprite %dx%d", sp.width, sp.height)
                else txt = txt .. "; no active sprite" end
                print("[ase-mcp] STATUS: " .. txt); app.alert{ title="ase-mcp", text=txt }
            end }
    end)
    pcall(connect)         -- connect; auto-reconnect catches it as it boots
end

function exit(plugin)
    disconnect()
end
