"""CUA Mouse and Keyboard Controller for Windows Desktop.

Provides mouse movement, clicks, dragging, scrolling, text typing,
and hotkey execution with fail-safes and multi-desktop station awareness.
"""

from __future__ import annotations

import ctypes
import logging
import time
from typing import Literal

import pyautogui

logger = logging.getLogger(__name__)

# Configure PyAutoGUI fail-safe (moving mouse to any corner raises exception)
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class MouseKeyboardController:
    """Controls mouse and keyboard inputs on Windows."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
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

    def get_screen_size(self) -> tuple[int, int]:
        """Get the resolution of the primary monitor."""
        self._ensure_interactive_desktop()
        return pyautogui.size()

    def get_mouse_position(self) -> tuple[int, int]:
        """Get the current cursor coordinates (x, y)."""
        self._ensure_interactive_desktop()
        pos = pyautogui.position()
        return (pos.x, pos.y)

    def _clamp_coordinates(self, x: int, y: int) -> tuple[int, int]:
        """Clamp coordinates within visible screen boundary."""
        screen_w, screen_h = self.get_screen_size()
        clamped_x = max(0, min(x, screen_w - 1))
        clamped_y = max(0, min(y, screen_h - 1))
        return clamped_x, clamped_y

    def move_to(self, x: int, y: int, duration: float = 0.0) -> tuple[int, int]:
        """Move cursor to coordinate (x, y)."""
        self._ensure_interactive_desktop()
        cx, cy = self._clamp_coordinates(x, y)
        pyautogui.moveTo(cx, cy, duration=duration)
        return (cx, cy)

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> tuple[int, int]:
        """Click at coordinate (x, y) or at the current cursor position."""
        self._ensure_interactive_desktop()
        if x is not None and y is not None:
            cx, cy = self._clamp_coordinates(x, y)
            pyautogui.click(x=cx, y=cy, button=button, clicks=clicks, interval=interval)
            return (cx, cy)
        else:
            pyautogui.click(button=button, clicks=clicks, interval=interval)
            return self.get_mouse_position()

    def double_click(self, x: int | None = None, y: int | None = None) -> tuple[int, int]:
        """Double click at coordinate (x, y) or current position."""
        return self.click(x=x, y=y, button="left", clicks=2, interval=0.15)

    def right_click(self, x: int | None = None, y: int | None = None) -> tuple[int, int]:
        """Right click at coordinate (x, y) or current position."""
        return self.click(x=x, y=y, button="right", clicks=1)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
        button: str = "left",
    ) -> None:
        """Drag from start coordinates to end coordinates."""
        self._ensure_interactive_desktop()
        sx, sy = self._clamp_coordinates(start_x, start_y)
        ex, ey = self._clamp_coordinates(end_x, end_y)
        pyautogui.moveTo(sx, sy)
        time.sleep(0.05)
        pyautogui.dragTo(ex, ey, duration=duration, button=button)

    def scroll(self, clicks: int = 3, direction: Literal["up", "down"] = "down") -> None:
        """Scroll mouse wheel vertically."""
        self._ensure_interactive_desktop()
        amount = -clicks if direction == "down" else clicks
        pyautogui.scroll(amount * 100)

    def type_text(self, text: str, delay: float = 0.01) -> None:
        """Type text string simulating keyboard strokes."""
        self._ensure_interactive_desktop()
        pyautogui.write(text, interval=delay)

    def press_key(self, key: str) -> None:
        """Press a single key (e.g. 'enter', 'esc', 'tab', 'space', 'backspace')."""
        self._ensure_interactive_desktop()
        pyautogui.press(key.lower())

    def hotkey(self, *keys: str) -> None:
        """Press a key combination (e.g. ('ctrl', 'c'), ('alt', 'tab'), ('win', 'r'))."""
        self._ensure_interactive_desktop()
        normalized_keys = [k.lower().strip() for k in keys]
        pyautogui.hotkey(*normalized_keys)


# Global controller singleton
mouse_keyboard = MouseKeyboardController()
