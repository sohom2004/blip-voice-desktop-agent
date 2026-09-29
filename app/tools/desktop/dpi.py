"""Windows Per-Monitor DPI Awareness Helper.

Enables Per-Monitor DPI Awareness v2 at process startup to ensure
that Windows UI Automation, MSS screen capture, and PyAutoGUI click coordinates
are aligned and not distorted by Windows display scaling (125%, 150%, 175%, etc.).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

_dpi_initialized = False
_dpi_info: dict[str, Any] = {}


def enable_per_monitor_dpi_awareness() -> dict[str, Any]:
    """Force Per-Monitor DPI Awareness v2 for the current process.

    Returns:
        dict with dpi status, awareness mode, system dpi, and scaling ratio.
    """
    global _dpi_initialized, _dpi_info
    if _dpi_initialized:
        return _dpi_info

    if sys.platform != "win32":
        _dpi_info = {
            "supported": False,
            "platform": sys.platform,
            "dpi": 96,
            "scale_factor": 1.0,
        }
        _dpi_initialized = True
        return _dpi_info

    user32 = ctypes.windll.user32
    awareness_set = False
    mode = "None"

    # 1. Try Per-Monitor DPI Awareness v2 (Windows 10 1703+)
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            res = user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            if res:
                awareness_set = True
                mode = "PerMonitorV2"
    except Exception as exc:
        logger.debug("SetProcessDpiAwarenessContext failed: %s", exc)

    # 2. Fallback to shcore: PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1+)
    if not awareness_set:
        try:
            shcore = ctypes.windll.shcore
            if hasattr(shcore, "SetProcessDpiAwareness"):
                res = shcore.SetProcessDpiAwareness(2)
                if res == 0:  # S_OK
                    awareness_set = True
                    mode = "PerMonitor"
        except Exception as exc:
            logger.debug("SetProcessDpiAwareness failed: %s", exc)

    # 3. Fallback to legacy user32.SetProcessDPIAware (Windows Vista+)
    if not awareness_set:
        try:
            if hasattr(user32, "SetProcessDPIAware"):
                res = user32.SetProcessDPIAware()
                if res:
                    awareness_set = True
                    mode = "SystemDPIAware"
        except Exception as exc:
            logger.debug("SetProcessDPIAware failed: %s", exc)

    # Detect current DPI and scaling factor
    system_dpi = 96
    try:
        if hasattr(user32, "GetDpiForSystem"):
            system_dpi = user32.GetDpiForSystem()
        else:
            hdc = user32.GetDC(0)
            if hdc:
                gdi32 = ctypes.windll.gdi32
                LOGPIXELSX = 88
                system_dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
                user32.ReleaseDC(0, hdc)
    except Exception as exc:
        logger.debug("Failed to query system DPI: %s", exc)

    scale_factor = round(system_dpi / 96.0, 3)

    _dpi_info = {
        "supported": True,
        "mode": mode,
        "awareness_set": awareness_set,
        "dpi": system_dpi,
        "scale_factor": scale_factor,
    }
    _dpi_initialized = True
    logger.info("DPI Awareness initialized: mode=%s, dpi=%d (%.1f%% scale)", mode, system_dpi, scale_factor * 100)
    return _dpi_info


def get_dpi_scale_factor() -> float:
    """Return the display scaling factor (e.g. 1.0 for 100%, 1.25 for 125%, 1.5 for 150%)."""
    info = enable_per_monitor_dpi_awareness()
    return float(info.get("scale_factor", 1.0))


# Enable on initial import
enable_per_monitor_dpi_awareness()
