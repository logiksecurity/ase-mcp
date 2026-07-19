"""
Build a distributable zip of ase-mcp into dist/ase-mcp-<version>.zip.
Also (re)builds the Aseprite extension. Run on any machine: `python build_package.py`.
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.0.0"
EXCLUDE_DIRS = {"__pycache__", "dist", ".git", ".venv", "venv", "build", "*.egg-info"}
EXCLUDE_FILES = {".env", "ase-mcp-server.exe"}


def build_extension() -> str:
    src = os.path.join(ROOT, "aseprite-plugin")
    out = os.path.join(ROOT, "ase-mcp-bridge.aseprite-extension")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(src, "package.json"), "package.json")
        z.write(os.path.join(src, "ase_bridge.lua"), "ase_bridge.lua")
        exe = os.path.join(ROOT, "ase-mcp-server.exe")
        if os.path.isfile(exe):
            z.write(exe, "ase-mcp-server.exe")   # bundled so the plugin can auto-launch it
    return out


def build_zip() -> str:
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, "ase-mcp-%s.zip" % VERSION)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDE_DIRS
                       and not d.endswith(".egg-info")
                       and not d.startswith("venv")
                       and not os.path.isfile(os.path.join(dp, d, "pyvenv.cfg"))]
            for f in files:
                if f in EXCLUDE_FILES:
                    continue
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, ROOT)
                z.write(full, os.path.join("ase-mcp", rel))
    return out


if __name__ == "__main__":
    ext = build_extension()
    print("extension:", os.path.relpath(ext, ROOT))
    z = build_zip()
    print("package:  ", os.path.relpath(z, ROOT), "(%d bytes)" % os.path.getsize(z))
