"""Desktop State Serializer for Jev System One.

Captures active windows, interactive UI elements, and user commands into a
structured, semantically rich state dictionary tailored for Jev's evaluation.
"""

from __future__ import annotations

import logging
from typing import Any

from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.window_manager import WindowInfo, window_manager
from app.workers.vision import vision_engine

logger = logging.getLogger(__name__)


class DesktopStateBuilder:
    """Builds clean, structured state snapshots for Jev System One evaluation."""

    def __init__(self):
        self._action_history: list[dict[str, Any]] = []

    def record_action(self, action_type: str, details: dict[str, Any]):
        """Record executed action to maintain turn history and prevent loops."""
        self._action_history.append(
            {
                "action": action_type,
                "details": details,
            }
        )
        if len(self._action_history) > 6:
            self._action_history.pop(0)

    def build_state(
        self,
        user_command: str,
        inspect_controls: bool = True,
        max_windows: int = 12,
        max_controls: int = 30,
    ) -> dict[str, Any]:
        """Collect current desktop state into a structured JSON representation."""
        # 1. Enumerate visible windows
        windows = window_manager.list_windows()
        active_win = window_manager.get_active_window()

        active_window_dict = None
        if active_win:
            proc = active_win.process_name.lower()
            if proc in vision_engine.TERMINAL_PROCESSES or "terminal" in proc:
                app_type = "terminal"
            elif proc in vision_engine.BROWSER_PROCESSES or "chrome" in proc or "brave" in proc:
                app_type = "browser"
            else:
                app_type = "desktop_app"

            active_window_dict = {
                "hwnd": active_win.hwnd,
                "title": active_win.title,
                "process": active_win.process_name,
                "app_type": app_type,
                "is_active": True,
            }

        open_windows_list = []
        for idx, w in enumerate(windows[:max_windows], start=1):
            open_windows_list.append(
                {
                    "id": f"win_{idx}",
                    "hwnd": w.hwnd,
                    "title": w.title,
                    "process": w.process_name,
                    "is_active": w.is_active,
                }
            )

        # 2. Sample UI elements from active window if requested
        interactive_elements = []
        if inspect_controls and active_win:
            elements = ui_inspector.inspect_window(active_win.hwnd, max_elements=max_controls)
            interactive_elements = ui_inspector.format_for_jev(elements)

        return {
            "user_command": user_command.strip(),
            "active_window": active_window_dict,
            "open_windows": open_windows_list,
            "interactive_elements": interactive_elements,
            "recent_actions": list(self._action_history[-3:]),
        }


# Global state builder singleton
state_builder = DesktopStateBuilder()
