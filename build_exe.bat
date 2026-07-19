@echo off
REM Build the standalone ase-mcp-server.exe (users need NO Python to run it).
REM Build machine needs Python 3.10+ with:
REM     pip install mcp uvicorn websockets pyinstaller
REM Then run this from the ase-mcp folder. Result: dist\ase-mcp-server.exe
REM
REM Note: we do NOT --collect-all mcp because mcp.cli sys.exit()s at import
REM (its optional 'typer' dep is absent). We collect mcp.server (FastMCP) instead.

python -m PyInstaller --onefile --name ase-mcp-server ^
  --collect-submodules mcp.server --collect-data mcp --exclude-module mcp.cli ^
  --hidden-import mcp.server.fastmcp ^
  --collect-all uvicorn ^
  --collect-submodules websockets --collect-submodules starlette --collect-submodules anyio ^
  server.py

echo.
echo Done. Executable: dist\ase-mcp-server.exe
