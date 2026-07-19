"""
Host helper: locate Aseprite (Steam install, drive-independent) and launch it with
the ase-mcp bridge script loaded. Run this ON THE HOST after `server.py` is up.

Resolution order for the Aseprite executable:
  1. env var ASE_ASEPRITE (absolute path to Aseprite.exe)
  2. Steam auto-detect: parse libraryfolders.vdf across all Steam libraries
  3. fallback default G:\\Program Files (x86)\\Steam\\steamapps\\common\\Aseprite\\Aseprite.exe
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_ASEPRITE = r"G:\Program Files (x86)\Steam\steamapps\common\Aseprite\Aseprite.exe"


def _steam_root() -> str | None:
    # Registry first (works whatever drive Steam is on), then common defaults.
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    val = winreg.QueryValueEx(k, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath")[0]
                    if val and os.path.isdir(val):
                        return val
            except OSError:
                continue
    except ImportError:
        pass
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(guess):
            return guess
    return None


def _steam_libraries(root: str) -> list[str]:
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(vdf):
        try:
            with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            # "path"    "D:\\SteamLibrary"
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                libs.append(m.group(1).replace("\\\\", "\\"))
        except OSError:
            pass
    return libs


def find_aseprite() -> str:
    env = os.getenv("ASE_ASEPRITE")
    if env and os.path.isfile(env):
        return env
    root = _steam_root()
    if root:
        for lib in _steam_libraries(root):
            cand = os.path.join(lib, "steamapps", "common", "Aseprite", "Aseprite.exe")
            if os.path.isfile(cand):
                return cand
    if os.path.isfile(DEFAULT_ASEPRITE):
        return DEFAULT_ASEPRITE
    raise FileNotFoundError(
        "Aseprite.exe not found. Set ASE_ASEPRITE to its full path."
    )


def main() -> int:
    exe = find_aseprite()
    print(f"Aseprite: {exe}")
    subprocess.Popen([exe])
    print("Opened Aseprite. The installed extension auto-connects to the server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
