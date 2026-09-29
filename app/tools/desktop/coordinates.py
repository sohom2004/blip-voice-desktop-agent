"""Normalized Coordinate Mapping and Transformation Pipeline.

Provides strict bidirectional coordinate transforms between:
- Physical display framebuffer pixels (MSS / Windows Direct3D)
- Logical virtual desktop coordinates (PyAutoGUI / User32)
- Downsampled / resized image spaces (Multimodal Vision / Gemini 1280px inputs)
- Normalized bounding boxes ([0.0, 1.0] and [0, 1000] Gemini formats)
- Window client space (ClientToScreen / ScreenToClient)
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

from app.tools.desktop.dpi import enable_per_monitor_dpi_awareness, get_dpi_scale_factor

logger = logging.getLogger(__name__)


class CoordinateTransformer:
    """Manages bidirectional coordinate mapping across display, window, and model spaces."""

    def __init__(self):
        enable_per_monitor_dpi_awareness()
        self.user32 = ctypes.windll.user32 if sys.platform == "win32" else None

    def get_screen_dimensions(self) -> tuple[int, int]:
        """Return the physical primary monitor resolution (width, height)."""
        if self.user32:
            # When DPI awareness is set, GetSystemMetrics returns physical display pixels
            SM_CXSCREEN = 0
            SM_CYSCREEN = 1
            w = self.user32.GetSystemMetrics(SM_CXSCREEN)
            h = self.user32.GetSystemMetrics(SM_CYSCREEN)
            if w > 0 and h > 0:
                return (w, h)

        # Fallback to pyautogui
        try:
            import pyautogui
            sz = pyautogui.size()
            return (sz.width, sz.height)
        except Exception:
            return (1920, 1080)

    def clamp(self, x: int, y: int, bounds: tuple[int, int] | None = None) -> tuple[int, int]:
        """Clamp coordinates within visible display dimensions."""
        screen_w, screen_h = bounds or self.get_screen_dimensions()
        cx = max(0, min(int(x), screen_w - 1))
        cy = max(0, min(int(y), screen_h - 1))
        return (cx, cy)

    def to_screen_coordinates(
        self,
        x: float,
        y: float,
        source_dims: tuple[int, int] | None = None,
        is_normalized_1000: bool = False,
        is_normalized_unit: bool = False,
    ) -> tuple[int, int]:
        """Convert coordinates from vision/model space into physical screen coordinates.

        Args:
            x: Horizontal coordinate (pixel, 0-1 unit, or 0-1000 integer).
            y: Vertical coordinate (pixel, 0-1 unit, or 0-1000 integer).
            source_dims: (width, height) of the image source if scaled/downsampled.
            is_normalized_1000: True if coordinates are Gemini [0, 1000] format.
            is_normalized_unit: True if coordinates are [0.0, 1.0] float fractions.

        Returns:
            (screen_x, screen_y) clamped to the screen resolution.
        """
        screen_w, screen_h = self.get_screen_dimensions()

        # 1. Gemini [0, 1000] bounding coordinate system
        if is_normalized_1000 or (isinstance(x, (int, float)) and x > 1.0 and x <= 1000 and y > 1.0 and y <= 1000 and source_dims is None):
            screen_x = int(round((x / 1000.0) * screen_w))
            screen_y = int(round((y / 1000.0) * screen_h))
            return self.clamp(screen_x, screen_y, (screen_w, screen_h))

        # 2. Unit normalized [0.0, 1.0] fraction
        if is_normalized_unit or (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and source_dims is None):
            screen_x = int(round(x * screen_w))
            screen_y = int(round(y * screen_h))
            return self.clamp(screen_x, screen_y, (screen_w, screen_h))

        # 3. Downsampled image coordinates (e.g. max_dim=1280)
        if source_dims is not None:
            src_w, src_h = source_dims
            if src_w > 0 and src_h > 0 and (src_w != screen_w or src_h != screen_h):
                scale_x = screen_w / float(src_w)
                scale_y = screen_h / float(src_h)
                screen_x = int(round(x * scale_x))
                screen_y = int(round(y * scale_y))
                return self.clamp(screen_x, screen_y, (screen_w, screen_h))

        # 4. Direct pixel coordinates
        return self.clamp(int(round(x)), int(round(y)), (screen_w, screen_h))

    def to_image_coordinates(
        self,
        screen_x: int,
        screen_y: int,
        target_dims: tuple[int, int],
    ) -> tuple[int, int]:
        """Convert physical screen coordinates to target resized image coordinates."""
        screen_w, screen_h = self.get_screen_dimensions()
        tgt_w, tgt_h = target_dims

        scale_x = float(tgt_w) / float(screen_w)
        scale_y = float(tgt_h) / float(screen_h)

        img_x = int(round(screen_x * scale_x))
        img_y = int(round(screen_y * scale_y))
        return (max(0, min(img_x, tgt_w - 1)), max(0, min(img_y, tgt_h - 1)))

    def client_to_screen(self, hwnd: int, client_x: int, client_y: int) -> tuple[int, int]:
        """Convert window client-area coordinates to absolute screen coordinates using Win32."""
        if not self.user32 or not hwnd:
            return self.clamp(client_x, client_y)

        try:
            pt = wintypes.POINT(int(client_x), int(client_y))
            if self.user32.ClientToScreen(hwnd, ctypes.byref(pt)):
                return (pt.x, pt.y)
        except Exception as exc:
            logger.debug("ClientToScreen failed for hwnd %d: %s", hwnd, exc)

        return self.clamp(client_x, client_y)

    def screen_to_client(self, hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
        """Convert absolute screen coordinates to window client-area coordinates using Win32."""
        if not self.user32 or not hwnd:
            return (screen_x, screen_y)

        try:
            pt = wintypes.POINT(int(screen_x), int(screen_y))
            if self.user32.ScreenToClient(hwnd, ctypes.byref(pt)):
                return (pt.x, pt.y)
        except Exception as exc:
            logger.debug("ScreenToClient failed for hwnd %d: %s", hwnd, exc)

        return (screen_x, screen_y)

    def assert_within_bounds(
        self,
        x: int,
        y: int,
        rect: tuple[int, int, int, int],
        tolerance: int = 2,
    ) -> bool:
        """Assert whether coordinates (x, y) fall within bounding rectangle (left, top, right, bottom).

        Tolerance accounts for sub-pixel anti-aliasing borders.
        """
        left, top, right, bottom = rect
        return (left - tolerance <= x <= right + tolerance) and (top - tolerance <= y <= bottom + tolerance)

    def get_box_center(self, rect: tuple[int, int, int, int]) -> tuple[int, int]:
        """Calculate center point of a bounding box (left, top, right, bottom)."""
        left, top, right, bottom = rect
        return ((left + right) // 2, (top + bottom) // 2)


# Global coordinate transformer singleton
coord_transformer = CoordinateTransformer()
