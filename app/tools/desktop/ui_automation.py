"""UI Automation Inspector for Windows Applications.

Extracts interactive accessibility elements (Buttons, Inputs, Tabs, Menus)
with bounding coordinates and center points for Jev System One decision making
and CUA click execution.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import asdict, dataclass
from typing import Any

import uiautomation as auto
from app.tools.desktop.window_manager import window_manager

logger = logging.getLogger(__name__)

INTERACTIVE_CONTROL_TYPES = {
    "ButtonControl",
    "EditControl",
    "MenuItemControl",
    "DocumentControl",
    "TabItemControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "ComboBoxControl",
    "HyperlinkControl",
    "ListItemControl",
    "TreeItemControl",
}


@dataclass
class UIElement:
    id: str  # e.g. "elem_1"
    name: str
    control_type: str
    automation_id: str
    rect: tuple[int, int, int, int]  # (left, top, right, bottom)
    center: tuple[int, int]  # (center_x, center_y)
    is_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UIAutomationInspector:
    """Inspects native Windows accessibility trees in a thread-safe manner."""

    def __init__(self):
        self.user32 = ctypes.windll.user32

    def _get_interactive_desktop_handle(self) -> int:
        DESKTOP_ALL = 0x01FF
        return self.user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)

    def inspect_window(self, hwnd: int, max_elements: int = 40, max_depth: int = 6) -> list[UIElement]:
        """Traverse accessibility tree of the window and return interactive controls."""
        results: list[UIElement] = []
        error_container: list[Exception] = []

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)

            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(hwnd)
                    if not root:
                        return

                    count = 0

                    def traverse(ctrl: auto.Control, depth: int):
                        nonlocal count
                        if depth > max_depth or count >= max_elements:
                            return

                        for child in ctrl.GetChildren():
                            if count >= max_elements:
                                break

                            ctype = child.ControlTypeName
                            rect = child.BoundingRectangle
                            w, h = rect.width(), rect.height()

                            if w > 0 and h > 0:
                                name = child.Name.strip()
                                auto_id = child.AutomationId.strip()

                                # Keep interactive elements or elements with meaningful names
                                if ctype in INTERACTIVE_CONTROL_TYPES and (name or auto_id):
                                    count += 1
                                    center_x = (rect.left + rect.right) // 2
                                    center_y = (rect.top + rect.bottom) // 2
                                    results.append(
                                        UIElement(
                                            id=f"elem_{count}",
                                            name=name,
                                            control_type=ctype.replace("Control", ""),
                                            automation_id=auto_id,
                                            rect=(rect.left, rect.top, rect.right, rect.bottom),
                                            center=(center_x, center_y),
                                            is_enabled=bool(child.IsEnabled),
                                        )
                                    )

                            traverse(child, depth + 1)

                    traverse(root, depth=0)
            except Exception as exc:
                logger.error("UI Automation inspection error: %s", exc)
                error_container.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=4.0)

        return results

    def inspect_active_window(self, max_elements: int = 40) -> tuple[int | None, str, list[UIElement]]:
        """Inspect the currently focused window."""
        active = window_manager.get_active_window()
        if not active:
            return None, "No active window", []
        elements = self.inspect_window(active.hwnd, max_elements=max_elements)
        return active.hwnd, active.title, elements

    @staticmethod
    def format_for_jev(elements: list[UIElement]) -> list[dict[str, Any]]:
        """Format element list into a compact structure optimal for Jev question rubrics."""
        formatted = []
        for el in elements:
            formatted.append(
                {
                    "id": el.id,
                    "label": el.name or el.automation_id or el.control_type,
                    "type": el.control_type,
                    "center": el.center,
                }
            )
        return formatted


# Global inspector singleton
ui_inspector = UIAutomationInspector()
