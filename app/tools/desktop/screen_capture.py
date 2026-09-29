"""Screen Capture and Visual Grounding Tool.

Provides high-speed desktop screenshot capture using MSS and PIL with
multi-desktop station awareness and base64 serialization for multimodal vision LLMs.
"""

from __future__ import annotations

import base64
import ctypes
import io
import logging
from pathlib import Path
from typing import Any

import mss
from PIL import Image

from app.tools.desktop.dpi import enable_per_monitor_dpi_awareness

logger = logging.getLogger(__name__)


class ScreenCaptureManager:
    """Manages high-speed desktop screenshot capture."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        enable_per_monitor_dpi_awareness()

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

    def capture_primary_monitor(self) -> Image.Image:
        """Capture the primary monitor as a PIL Image."""
        self._ensure_interactive_desktop()
        try:
            with mss.MSS() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                return img
        except Exception as exc:
            logger.warning("Primary monitor capture failed: %s. Using fallback.", exc)
            try:
                from PIL import ImageGrab
                return ImageGrab.grab()
            except Exception:
                w, h = 1920, 1080
                try:
                    import pyautogui
                    w, h = pyautogui.size()
                except Exception:
                    pass
                return Image.new("RGB", (w, h), color=(25, 25, 30))

    def capture_to_file(self, output_path: str | Path) -> Path:
        """Capture primary monitor and save to disk."""
        img = self.capture_primary_monitor()
        p = Path(output_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p, format="PNG")
        return p

    def capture_to_base64(self, max_width: int = 1920, quality: int = 85) -> str:
        """Capture primary monitor and return as base64 JPEG data URL for multimodal models."""
        img = self.capture_primary_monitor()
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_height = int(float(img.height) * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"

    def capture_region(self, rect: tuple[int, int, int, int]) -> Image.Image:
        """Capture a specific bounding box (left, top, right, bottom)."""
        self._ensure_interactive_desktop()
        left, top, right, bottom = rect
        bbox = {
            "left": left,
            "top": top,
            "width": max(1, right - left),
            "height": max(1, bottom - top),
        }
        with mss.MSS() as sct:
            sct_img = sct.grab(bbox)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            return img


# Global screen capture singleton
screen_capture = ScreenCaptureManager()
