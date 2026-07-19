# ase-mcp-keepalive.ps1
# Aseprite throttles its own event loop (and thus WebSocket callbacks) when the
# mouse is outside its window, so ase-mcp commands stall until you move the mouse
# over Aseprite. This posts synthetic mouse-move + repaint messages to the Aseprite
# window WITHOUT moving your real cursor, keeping the loop pumping so an AI agent can
# drive Aseprite hands-off from the VM. Run on the HOST. Ctrl+C to stop.
#
#   .\scripts\ase-mcp-keepalive.ps1              # default 400 ms
#   .\scripts\ase-mcp-keepalive.ps1 -IntervalMs 250

param([int]$IntervalMs = 400)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class KeepAlive {
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool RedrawWindow(IntPtr hWnd, IntPtr lprc, IntPtr hrgn, uint flags);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

$WM_MOUSEMOVE   = 0x0200
$RDW_INVALIDATE = 0x0001
$RDW_UPDATENOW  = 0x0100

Write-Host ""
Write-Host "ase-mcp keep-alive: pumping Aseprite every $IntervalMs ms. Ctrl+C to stop." -ForegroundColor Cyan
$missing = $true
while ($true) {
    $p = Get-Process aseprite -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if ($p) {
        if ($missing) { Write-Host "[OK] Aseprite found (pid $($p.Id)) - keeping it awake" -ForegroundColor Green; $missing = $false }
        $h = $p.MainWindowHandle
        $r = New-Object KeepAlive+RECT
        [void][KeepAlive]::GetClientRect($h, [ref]$r)
        $x = [int](($r.Right - $r.Left) / 2)
        $y = [int](($r.Bottom - $r.Top) / 2)
        $lparam = [IntPtr]((($y -band 0xFFFF) -shl 16) -bor ($x -band 0xFFFF))
        [void][KeepAlive]::PostMessage($h, $WM_MOUSEMOVE, [IntPtr]::Zero, $lparam)
        [void][KeepAlive]::RedrawWindow($h, [IntPtr]::Zero, [IntPtr]::Zero, ($RDW_INVALIDATE -bor $RDW_UPDATENOW))
    } elseif (-not $missing) {
        Write-Host "[..] Aseprite not running, waiting..." -ForegroundColor Yellow; $missing = $true
    }
    Start-Sleep -Milliseconds $IntervalMs
}
