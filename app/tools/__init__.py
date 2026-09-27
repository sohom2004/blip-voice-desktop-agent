"""Unified Tools Suite for Desktop, Browser, and System Control.

Integrates tools inspired by:
- yikangy873-gif/jev-desktop (UI Automation & Win32 Window Management)
- wy-coliney/jev-browser-use (CDP & DOM Navigation)
- nousresearch/hermes-agent (Terminal & Process Execution)
- earendil-works/pi (Resilient File Editing & Inspection)
"""

from app.tools.browser.browser_manager import browser_manager
from app.tools.desktop.mouse_keyboard import mouse_keyboard
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.window_manager import window_manager
from app.tools.system.file_ops import file_manager
from app.tools.system.terminal import terminal_manager

__all__ = [
    "window_manager",
    "mouse_keyboard",
    "ui_inspector",
    "screen_capture",
    "browser_manager",
    "terminal_manager",
    "file_manager",
]
