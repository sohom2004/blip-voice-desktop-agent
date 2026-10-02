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

from app.tools.desktop.coordinates import coord_transformer
from app.tools.desktop.dpi import enable_per_monitor_dpi_awareness

logger = logging.getLogger(__name__)

# Configure PyAutoGUI fail-safe (moving mouse to any corner raises exception)
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class MouseKeyboardController:
    """Controls mouse and keyboard inputs on Windows with DPI scaling awareness and assertions."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        enable_per_monitor_dpi_awareness()
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
        """Get the physical resolution of the primary monitor."""
        self._ensure_interactive_desktop()
        return coord_transformer.get_screen_dimensions()

    def get_mouse_position(self) -> tuple[int, int]:
        """Get the current cursor coordinates (x, y)."""
        self._ensure_interactive_desktop()
        pos = pyautogui.position()
        return (pos.x, pos.y)

    def _clamp_coordinates(self, x: int, y: int) -> tuple[int, int]:
        """Clamp coordinates within visible screen boundary."""
        return coord_transformer.clamp(x, y)

    def move_to(self, x: int, y: int, duration: float = 0.0) -> tuple[int, int]:
        """Move cursor to coordinate (x, y) with clamping."""
        self._ensure_interactive_desktop()
        cx, cy = self._clamp_coordinates(x, y)
        pyautogui.moveTo(cx, cy, duration=duration)
        return (cx, cy)

    def assert_cursor_position(
        self,
        expected_x: int,
        expected_y: int,
        target_rect: tuple[int, int, int, int] | None = None,
    ) -> bool:
        """Verify that current cursor position matches target coordinates or falls within bounding box."""
        pos = pyautogui.position()
        actual_x, actual_y = pos.x, pos.y

        if target_rect:
            left, top, right, bottom = target_rect
            in_rect = coord_transformer.assert_within_bounds(actual_x, actual_y, target_rect)
            if not in_rect:
                logger.warning(
                    "Cursor assertion failed: cursor at (%d, %d) outside expected bounding box (%d, %d, %d, %d)",
                    actual_x, actual_y, left, top, right, bottom
                )
                return False
            logger.info("Cursor asserted at (%d, %d) within target bounding box", actual_x, actual_y)
            return True

        dist = max(abs(actual_x - expected_x), abs(actual_y - expected_y))
        if dist > 3:
            logger.warning("Cursor assertion warning: cursor at (%d, %d), expected (%d, %d), delta=%d", actual_x, actual_y, expected_x, expected_y, dist)
            return False
        return True

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        interval: float = 0.1,
        target_rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int]:
        """Click at coordinate (x, y) or at the current cursor position with position verification."""
        self._ensure_interactive_desktop()
        if x is not None and y is not None:
            cx, cy = self._clamp_coordinates(x, y)
            pyautogui.moveTo(cx, cy)
            time.sleep(0.01)
            self.assert_cursor_position(cx, cy, target_rect=target_rect)
            pyautogui.click(x=cx, y=cy, button=button, clicks=clicks, interval=interval)
            return (cx, cy)
        else:
            pyautogui.click(button=button, clicks=clicks, interval=interval)
            return self.get_mouse_position()

    def click_with_assertion(
        self,
        x: int,
        y: int,
        target_rect: tuple[int, int, int, int] | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        """Click at (x, y) and return diagnostic assertion payload."""
        cx, cy = self.click(x=x, y=y, button=button, clicks=clicks, target_rect=target_rect)
        pos = self.get_mouse_position()
        return {
            "x": cx,
            "y": cy,
            "actual_cursor": pos,
            "asserted": target_rect is not None and coord_transformer.assert_within_bounds(pos[0], pos[1], target_rect),
            "target_rect": target_rect,
        }

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

    def press_hotkey(self, keys: list[str] | str) -> None:
        """Alias for hotkey/press_key accepting a list of key names or single key."""
        if isinstance(keys, str):
            self.press_key(keys)
        elif len(keys) == 1:
            self.press_key(keys[0])
        else:
            self.hotkey(*keys)


# Global controller singleton
mouse_keyboard = MouseKeyboardController()
