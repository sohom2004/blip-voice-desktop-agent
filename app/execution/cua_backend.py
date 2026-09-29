"""Optional Computer-Use Agent (CUA) Backend.

Provides background-first desktop interaction:
1. Detects external `cua-driver` if available on the system.
2. Provides native Win32 background message posting (WM_LBUTTONDOWN/UP, WM_CHAR)
   that interacts with windows WITHOUT stealing physical mouse cursor or foreground focus.
3. Serves as a non-breaking optional backend within the Hybrid Execution ladder.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

import win32con
import win32gui

logger = logging.getLogger(__name__)


class CuaBackend:
    """Background-first interaction backend (Hermes cua-driver & Win32 messaging)."""

    def __init__(self):
        self._has_cua_driver = shutil.which("cua") is not None or shutil.which("cua-driver") is not None
        if self._has_cua_driver:
            logger.info("Found external CUA driver executable on PATH.")
        else:
            logger.info("External CUA driver not on PATH. Utilizing native Win32 background messaging mode.")

    def is_available(self) -> bool:
        """Check if any background CUA mode is operational."""
        return True  # Win32 background messaging is always available on Windows

    def has_external_driver(self) -> bool:
        """Check if dedicated Hermes cua-driver binary exists."""
        return self._has_cua_driver

    def click_background(self, hwnd: int, x: int, y: int) -> dict[str, Any]:
        """Click at (x, y) relative to window client coordinates without moving physical cursor."""
        if not win32gui.IsWindow(hwnd):
            return {"success": False, "error": f"Invalid HWND {hwnd}."}

        try:
            # Calculate lParam (LOWORD = x, HIWORD = y)
            lParam = (y << 16) | (x & 0xFFFF)
            win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lParam)
            win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lParam)
            logger.info("Sent background click to HWND %d at (%d, %d)", hwnd, x, y)
            return {"success": True, "method": "Win32_PostMessage_Click", "coords": (x, y)}
        except Exception as exc:
            logger.error("Failed background click: %s", exc)
            return {"success": False, "error": str(exc)}

    def type_background(self, hwnd: int, text: str) -> dict[str, Any]:
        """Send characters directly to window message queue without stealing foreground focus."""
        if not win32gui.IsWindow(hwnd):
            return {"success": False, "error": f"Invalid HWND {hwnd}."}

        try:
            for char in text:
                win32gui.PostMessage(hwnd, win32con.WM_CHAR, ord(char), 0)
            logger.info("Sent %d characters via background WM_CHAR to HWND %d", len(text), hwnd)
            return {"success": True, "method": "Win32_PostMessage_WM_CHAR", "length": len(text)}
        except Exception as exc:
            logger.error("Failed background type: %s", exc)
            return {"success": False, "error": str(exc)}

    def execute_cua_driver_cmd(self, args: list[str]) -> dict[str, Any]:
        """Execute command with Hermes cua-driver binary if available."""
        if not self._has_cua_driver:
            return {"success": False, "error": "External cua-driver not installed."}

        cmd_name = "cua" if shutil.which("cua") else "cua-driver"
        try:
            res = subprocess.run([cmd_name, *args], capture_output=True, text=True, timeout=5.0)
            return {
                "success": res.returncode == 0,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "exit_code": res.returncode,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# Global CUA backend singleton
cua_backend = CuaBackend()
