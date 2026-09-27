"""Windows Desktop Window Manager.

Provides comprehensive window discovery, filtering, focusing, and state management
using Win32 APIs with multi-desktop station awareness.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

import psutil
import win32con
import win32gui
import win32process

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    pid: int
    rect: tuple[int, int, int, int]  # (left, top, right, bottom)
    width: int
    height: int
    is_minimized: bool
    is_active: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WindowManager:
    """Manages discovery and manipulation of Windows desktop application windows."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self._ensure_interactive_desktop()

    def _ensure_interactive_desktop(self) -> bool:
        """Attach calling thread to the interactive 'Default' desktop on WinSta0."""
        try:
            DESKTOP_ALL = 0x01FF
            h_default = self.user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
            if h_default:
                self.user32.SetThreadDesktop(h_default)
                return True
        except Exception as exc:
            logger.debug("Failed to set thread desktop to Default: %s", exc)
        return False

    def is_valid_window(self, hwnd: int) -> bool:
        """Filter out hidden, zero-sized, and background worker windows."""
        if not win32gui.IsWindow(hwnd):
            return False
        if not win32gui.IsWindowVisible(hwnd):
            return False

        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return False

        # Exclude shell utility windows, input methods, and overlays
        excluded_titles = {
            "Program Manager",
            "Default IME",
            "MSCTFIME UI",
            "Windows Input Experience",
            "NVIDIA GeForce Overlay",
            "Task Switching",
            "PopupHost",
        }
        if title in excluded_titles:
            return False

        # Exclude tool/owned child windows that are not top-level
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return False

        # Exclude windows with negligible dimensions (unless minimized)
        is_iconic = bool(win32gui.IsIconic(hwnd))
        if not is_iconic:
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            if width <= 80 or height <= 80:
                return False

        # Check cloaked windows (Windows 10/11 virtual desktops or minimized UWP apps)
        is_cloaked = ctypes.c_int(0)
        DWMWA_CLOAKED = 14
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(is_cloaked), ctypes.sizeof(is_cloaked)
            )
            if is_cloaked.value != 0:
                return False
        except Exception:
            pass

        return True

    def get_process_name(self, pid: int) -> str:
        """Lookup process executable name from PID."""
        try:
            return psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return "unknown"

    def list_windows(self) -> list[WindowInfo]:
        """Enumerate all open, visible top-level application windows."""
        self._ensure_interactive_desktop()
        active_hwnd = win32gui.GetForegroundWindow()
        windows: list[WindowInfo] = []

        hwnds: list[int] = []

        def enum_callback(hwnd: int, _lparam: Any) -> bool:
            hwnds.append(hwnd)
            return True

        from ctypes import wintypes
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        c_proc = WNDENUMPROC(enum_callback)

        DESKTOP_ALL = 0x01FF
        h_default = self.user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
        if h_default:
            self.user32.SetThreadDesktop(h_default)
            self.user32.EnumDesktopWindows(h_default, c_proc, 0)
        else:
            self.user32.EnumWindows(c_proc, 0)

        for hwnd in hwnds:
            try:
                if not self.is_valid_window(hwnd):
                    continue

                title = win32gui.GetWindowText(hwnd).strip()
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                rect = win32gui.GetWindowRect(hwnd)
                is_minimized = bool(win32gui.IsIconic(hwnd))
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                is_active = (hwnd == active_hwnd)

                windows.append(
                    WindowInfo(
                        hwnd=hwnd,
                        title=title,
                        process_name=self.get_process_name(pid),
                        pid=pid,
                        rect=rect,
                        width=width,
                        height=height,
                        is_minimized=is_minimized,
                        is_active=is_active,
                    )
                )
            except Exception as exc:
                logger.debug("Failed to inspect hwnd %s: %s", hwnd, exc)

        return windows

    def get_active_window(self) -> WindowInfo | None:
        """Get the currently focused foreground window."""
        self._ensure_interactive_desktop()
        active_hwnd = win32gui.GetForegroundWindow()
        if not active_hwnd or not win32gui.IsWindow(active_hwnd):
            return None

        title = win32gui.GetWindowText(active_hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(active_hwnd)
        rect = win32gui.GetWindowRect(active_hwnd)
        return WindowInfo(
            hwnd=active_hwnd,
            title=title,
            process_name=self.get_process_name(pid),
            pid=pid,
            rect=rect,
            width=rect[2] - rect[0],
            height=rect[3] - rect[1],
            is_minimized=bool(win32gui.IsIconic(active_hwnd)),
            is_active=True,
        )

    def find_window(self, query: str | int) -> WindowInfo | None:
        """Find a window by HWND, exact title, substring match, or process name."""
        windows = self.list_windows()
        if not windows:
            return None

        # Check by HWND if integer or numeric
        if isinstance(query, int):
            for w in windows:
                if w.hwnd == query:
                    return w
            return None

        query_str = str(query).strip().lower()

        # Try exact HWND string
        if query_str.isdigit():
            hwnd_val = int(query_str)
            for w in windows:
                if w.hwnd == hwnd_val:
                    return w

        # 1. Exact title match (case-insensitive)
        for w in windows:
            if w.title.lower() == query_str:
                return w

        # 2. Process name match (e.g. "chrome", "code", "spotify", "notepad")
        for w in windows:
            p_base = w.process_name.lower().replace(".exe", "")
            if query_str == p_base or query_str in p_base:
                return w

        # 3. Substring in title
        for w in windows:
            if query_str in w.title.lower():
                return w

        return None

    def bring_to_front(self, query: str | int) -> bool:
        """Forcefully bring the specified window to the foreground."""
        self._ensure_interactive_desktop()
        target = self.find_window(query)
        if not target:
            logger.warning("Window not found for query: %s", query)
            return False

        hwnd = target.hwnd

        try:
            # 1. Restore if minimized
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            # 2. Attach thread input trick to bypass Windows focus restrictions
            foreground_hwnd = win32gui.GetForegroundWindow()
            curr_thread = self.kernel32.GetCurrentThreadId()
            fg_thread = self.user32.GetWindowThreadProcessId(foreground_hwnd, None)
            target_thread = self.user32.GetWindowThreadProcessId(hwnd, None)

            if fg_thread != target_thread:
                self.user32.AttachThreadInput(curr_thread, target_thread, True)
                if fg_thread:
                    self.user32.AttachThreadInput(fg_thread, target_thread, True)

            # 3. Force foreground and bring to top
            self.user32.BringWindowToTop(hwnd)
            self.user32.SetForegroundWindow(hwnd)

            # Detach threads
            if fg_thread != target_thread:
                self.user32.AttachThreadInput(curr_thread, target_thread, False)
                if fg_thread:
                    self.user32.AttachThreadInput(fg_thread, target_thread, False)

            logger.info("Brought window to front: '%s' (HWND %d)", target.title, hwnd)
            return True
        except Exception as exc:
            logger.error("Failed to bring window to front (%d): %s", hwnd, exc)
            return False

    def minimize_window(self, query: str | int) -> bool:
        """Minimize a window by query."""
        self._ensure_interactive_desktop()
        target = self.find_window(query)
        if not target:
            return False
        win32gui.ShowWindow(target.hwnd, win32con.SW_MINIMIZE)
        return True

    def maximize_window(self, query: str | int) -> bool:
        """Maximize a window by query."""
        self._ensure_interactive_desktop()
        target = self.find_window(query)
        if not target:
            return False
        win32gui.ShowWindow(target.hwnd, win32con.SW_MAXIMIZE)
        return True

    def close_window(self, query: str | int) -> bool:
        """Send close message to window."""
        self._ensure_interactive_desktop()
        target = self.find_window(query)
        if not target:
            return False
        win32gui.PostMessage(target.hwnd, win32con.WM_CLOSE, 0, 0)
        return True

    def wait_for_window(self, query: str | int, timeout: float = 5.0) -> WindowInfo | None:
        """Poll until a window matching query appears or timeout expires."""
        start = time.time()
        while time.time() - start < timeout:
            win = self.find_window(query)
            if win:
                return win
            time.sleep(0.2)
        return None

    def wait_for_window_close(self, query: str | int, timeout: float = 5.0) -> bool:
        """Poll until a window matching query is closed or timeout expires."""
        start = time.time()
        while time.time() - start < timeout:
            win = self.find_window(query)
            if not win:
                return True
            time.sleep(0.2)
        return False

    def launch_app(self, app_name: str, wait_for_window: bool = True, timeout: float = 3.5) -> bool:
        """Launch an application and verify that its window opens and is focused."""
        import os
        import subprocess

        name_clean = app_name.strip().lower()
        for prefix in ("open ", "launch ", "start "):
            if name_clean.startswith(prefix):
                name_clean = name_clean[len(prefix):].strip()

        app_aliases = {
            "calculator": "calc",
            "calc": "calc",
            "notepad": "notepad",
            "chrome": "chrome",
            "google chrome": "chrome",
            "brave": "brave",
            "edge": "msedge",
            "microsoft edge": "msedge",
            "vs code": "code",
            "vscode": "code",
            "code": "code",
            "terminal": "wt",
            "windows terminal": "wt",
            "powershell": "powershell",
            "cmd": "cmd",
            "command prompt": "cmd",
            "settings": "ms-settings:",
            "spotify": "spotify",
            "explorer": "explorer",
            "file explorer": "explorer",
            "task manager": "taskmgr",
        }
        target = app_aliases.get(name_clean, name_clean)
        try:
            subprocess.Popen(f"start {target}", shell=True)
            logger.info("Dispatched application launch: %s (target: %s)", app_name, target)

            if not wait_for_window:
                return True

            # Poll for window appearance and bring to front
            start_t = time.time()
            while time.time() - start_t < timeout:
                time.sleep(0.25)
                matched = self.find_window(target) or self.find_window(name_clean)
                if matched:
                    self.bring_to_front(matched.hwnd)
                    logger.info("Verified window opened and brought to front: '%s' (HWND %d)", matched.title, matched.hwnd)
                    return True

            logger.info("Launched %s (target %s), process started (window verification timed out after %.1fs)", app_name, target, timeout)
            return True
        except Exception as exc:
            logger.error("Failed to launch application %s: %s", app_name, exc)
            return False


# Global window manager singleton
window_manager = WindowManager()
