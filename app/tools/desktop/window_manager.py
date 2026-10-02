"""Windows Desktop Window Manager.

Provides comprehensive window discovery, filtering, focusing, and state management
using Win32 APIs with multi-desktop station awareness.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import re
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
    class_name: str = ""
    category: str = "app"
    index: int = 1
    alias: str = ""

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

    @staticmethod
    def classify_window(process_name: str, class_name: str, title: str) -> str:
        """Classify a window into a canonical user-facing application category."""
        proc = (process_name or "").lower().replace(".exe", "")
        cls = (class_name or "").lower()
        ttl = (title or "").lower()

        # 1. Terminals
        terminal_procs = {
            "windowsterminal", "powershell", "pwsh", "cmd", "conhost",
            "alacritty", "wezterm-gui", "wezterm", "kitty", "bash", "wsl",
        }
        if proc in terminal_procs or "cascadia" in cls or "consolewindowclass" in cls:
            return "terminal"
        if "command prompt" in ttl or "powershell" in ttl or "terminal" in ttl or "pwsh" in ttl:
            return "terminal"

        # 2. File Explorer
        if proc == "explorer" and ("cabinetwclass" in cls or "explorewclass" in cls or "file explorer" in ttl):
            return "explorer"

        # 3. Code Editors / IDEs
        code_procs = {"code", "cursor", "antigravity", "devenv", "pycharm64", "idea64", "webstorm"}
        if proc in code_procs:
            return "code"

        # 4. Text Editors
        editor_procs = {"notepad", "notepad++", "sublime_text"}
        if proc in editor_procs:
            return "editor"

        # 5. Web Browsers
        browser_procs = {"chrome", "msedge", "brave", "firefox", "opera", "vivaldi", "arc"}
        if proc in browser_procs:
            return "browser"

        return proc or "app"

    def list_windows(self) -> list[WindowInfo]:
        """List all visible top-level windows in active Z-order, with canonical aliases."""
        self._ensure_interactive_desktop()
        windows: list[WindowInfo] = []
        active_hwnd = win32gui.GetForegroundWindow()

        hwnds: list[int] = []

        def enum_callback(hwnd, _):
            hwnds.append(hwnd)
            return True

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

                try:
                    class_name = win32gui.GetClassName(hwnd)
                except Exception:
                    class_name = ""

                proc_name = self.get_process_name(pid)
                windows.append(
                    WindowInfo(
                        hwnd=hwnd,
                        title=title,
                        process_name=proc_name,
                        pid=pid,
                        rect=rect,
                        width=width,
                        height=height,
                        is_minimized=is_minimized,
                        is_active=is_active,
                        class_name=class_name,
                    )
                )
            except Exception as exc:
                logger.debug("Failed to inspect hwnd %s: %s", hwnd, exc)

        # Assign indexed categories and aliases in Z-order (topmost active = 1)
        category_counts: dict[str, int] = {}
        for w in windows:
            cat = self.classify_window(w.process_name, w.class_name, w.title)
            count = category_counts.get(cat, 0) + 1
            category_counts[cat] = count
            w.category = cat
            w.index = count
            w.alias = f"{cat} {count}"

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
        try:
            class_name = win32gui.GetClassName(active_hwnd)
        except Exception:
            class_name = ""

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
            class_name=class_name,
        )

    def is_window_active(self, hwnd: int) -> bool:
        """Check if the given window handle is currently the active foreground window."""
        self._ensure_interactive_desktop()
        return win32gui.GetForegroundWindow() == hwnd

    def is_window_visible(self, hwnd: int) -> bool:
        """Check if the given window handle exists and is visible."""
        return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))

    def wait_for_window_state(
        self,
        hwnd: int,
        state: str = "active",
        timeout: float = 5.0,
    ) -> bool:
        """Wait for window to reach an expected state: 'active', 'visible', 'minimized', 'closed'."""
        start_t = time.time()
        while time.time() - start_t < timeout:
            if state == "active":
                if self.is_window_active(hwnd):
                    return True
            elif state == "visible":
                if self.is_window_visible(hwnd):
                    return True
            elif state == "minimized":
                if win32gui.IsWindow(hwnd) and win32gui.IsIconic(hwnd):
                    return True
            elif state == "closed":
                if not win32gui.IsWindow(hwnd):
                    return True
            time.sleep(0.15)
        return False

    def find_window_by_spec(
        self,
        hwnd: int | None = None,
        title: str | None = None,
        process_name: str | None = None,
        class_name: str | None = None,
        pid: int | None = None,
    ) -> WindowInfo | None:
        """Find a window matching specific structured attributes."""
        windows = self.list_windows()
        for w in windows:
            if hwnd is not None and w.hwnd != hwnd:
                continue
            if pid is not None and w.pid != pid:
                continue
            if process_name is not None:
                p_clean = process_name.lower().replace(".exe", "")
                w_proc = w.process_name.lower().replace(".exe", "")
                if p_clean != w_proc and p_clean not in w_proc:
                    continue
            if class_name is not None:
                if class_name.lower() != w.class_name.lower():
                    continue
            if title is not None:
                t_clean = title.lower()
                if t_clean != w.title.lower() and t_clean not in w.title.lower():
                    continue
            return w
        return None

    def find_window(self, query: str | int) -> WindowInfo | None:
        """Find a window by HWND, indexed alias ('terminal 1', 'terminal 2', 'explorer 1'), exact title, substring match, or process name."""
        windows = self.list_windows()
        if not windows:
            return None

        # Check by HWND if integer
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

        # Normalize written and ordinal numbers (e.g. "terminal one" -> "terminal 1", "second terminal" -> "terminal 2")
        num_map = {
            "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        }
        # Handle "first terminal" -> "terminal 1"
        for word, digit in num_map.items():
            query_str = re.sub(rf"\b{word}\s+([a-z_]+)\b", rf"\1 {digit}", query_str)
            query_str = re.sub(rf"\b{word}\b", digit, query_str)

        # 1. Match indexed alias pattern (e.g. "terminal 1", "terminal 2", "explorer 1", "browser 2")
        idx_match = re.match(r"^([a-z_]+)\s*(\d+)$", query_str)
        if idx_match:
            cat_query = idx_match.group(1)
            target_idx = int(idx_match.group(2))
            for w in windows:
                # Match alias directly ("terminal 1")
                if w.alias.lower() == f"{cat_query} {target_idx}":
                    return w
                # Match category and index
                if (w.category == cat_query or w.process_name.lower().replace(".exe", "") == cat_query) and w.index == target_idx:
                    return w

        # 2. Exact alias match
        for w in windows:
            if w.alias.lower() == query_str:
                return w

        # 3. Exact title match (case-insensitive)
        for w in windows:
            if w.title.lower() == query_str:
                return w

        # 4. If query is a general category without index (e.g. "terminal", "explorer", "browser"),
        # return the first instance (terminal 1, explorer 1)
        for w in windows:
            if w.category == query_str and w.index == 1:
                return w

        # 5. Process name match (e.g. "chrome", "code", "spotify", "notepad")
        for w in windows:
            p_base = w.process_name.lower().replace(".exe", "")
            if query_str == p_base or query_str in p_base:
                return w

        # 6. Substring in title
        for w in windows:
            if query_str in w.title.lower():
                return w

        return None

    def send_input_to_window(
        self,
        query: str | int,
        text: str,
        press_enter: bool = True,
    ) -> dict[str, Any]:
        """Focus a target window (e.g. 'terminal 1', 'terminal 2', HWND) and type text into it."""
        target = self.find_window(query)
        if not target:
            return {"success": False, "error": f"Window '{query}' not found."}

        brought = self.bring_to_front(target.hwnd)
        if not brought:
            return {"success": False, "error": f"Could not bring window '{target.title}' ({target.alias}) to foreground."}

        time.sleep(0.15)
        from app.tools.desktop.mouse_keyboard import mouse_keyboard
        mouse_keyboard.type_text(text)
        if press_enter:
            time.sleep(0.05)
            mouse_keyboard.press_hotkey(["enter"])

        return {
            "success": True,
            "window": target.alias or target.title,
            "hwnd": target.hwnd,
            "typed": text,
        }

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
