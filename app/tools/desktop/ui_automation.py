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
from app.tools.desktop.dpi import enable_per_monitor_dpi_awareness
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
    "TextControl",
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
    hwnd: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UIAutomationInspector:
    """Inspects native Windows accessibility trees in a thread-safe manner."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        enable_per_monitor_dpi_awareness()
        self._element_cache: dict[str, UIElement] = {}

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
                                if ctype == "TextControl" and (len(name) < 3 or (name and 0xE000 <= ord(name[0]) <= 0xF8FF)):
                                    continue

                                if ctype in INTERACTIVE_CONTROL_TYPES and (name or auto_id):
                                    count += 1
                                    center_x = (rect.left + rect.right) // 2
                                    center_y = (rect.top + rect.bottom) // 2
                                    elem = UIElement(
                                        id=f"elem_{count}",
                                        name=name,
                                        control_type=ctype.replace("Control", ""),
                                        automation_id=auto_id,
                                        rect=(rect.left, rect.top, rect.right, rect.bottom),
                                        center=(center_x, center_y),
                                        is_enabled=bool(child.IsEnabled),
                                        hwnd=hwnd,
                                    )
                                    results.append(elem)
                                    self._element_cache[elem.id] = elem

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

    def get_element(self, element_id: str) -> UIElement | None:
        """Retrieve cached UIElement by ID."""
        return self._element_cache.get(element_id)

    def invoke_element(self, element_id: str) -> dict[str, Any]:
        """Invoke or click an element using direct Windows UI Automation patterns,
        with coordinate fallback and cursor position assertion.
        """
        el = self._element_cache.get(element_id)
        if not el:
            return {"success": False, "error": f"Element '{element_id}' not found in inspection cache."}

        invoke_result = {"success": False, "method": "none", "element": el.to_dict()}
        error_container: list[Exception] = []

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)

            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return

                    target_ctrl = None

                    # 1. Search by AutomationId if available
                    if el.automation_id:
                        target_ctrl = root.FindControl(
                            lambda c, d: c.AutomationId == el.automation_id and c.ControlTypeName.replace("Control", "") == el.control_type,
                            maxDepth=8,
                        )

                    # 2. Search by matching bounding box center
                    if not target_ctrl:
                        target_ctrl = root.FindControl(
                            lambda c, d: (
                                abs((c.BoundingRectangle.left + c.BoundingRectangle.right) // 2 - el.center[0]) <= 8
                                and abs((c.BoundingRectangle.top + c.BoundingRectangle.bottom) // 2 - el.center[1]) <= 8
                                and c.ControlTypeName.replace("Control", "") == el.control_type
                            ),
                            maxDepth=8,
                        )

                    # 3. Search by exact name and control type
                    if not target_ctrl and el.name:
                        target_ctrl = root.FindControl(
                            lambda c, d: c.Name.strip() == el.name and c.ControlTypeName.replace("Control", "") == el.control_type,
                            maxDepth=8,
                        )

                    if target_ctrl:
                        # Attempt Pattern 1: InvokePattern
                        try:
                            inv_pat = target_ctrl.GetPattern(auto.PatternId.InvokePattern)
                            if inv_pat and hasattr(inv_pat, "Invoke"):
                                inv_pat.Invoke()
                                invoke_result["success"] = True
                                invoke_result["method"] = "InvokePattern"
                                logger.info("Successfully invoked '%s' via UIA InvokePattern", el.name)
                                return
                        except Exception as exc:
                            logger.debug("InvokePattern failed for '%s': %s", el.name, exc)

                        # Attempt Pattern 2: TogglePattern
                        try:
                            tog_pat = target_ctrl.GetPattern(auto.PatternId.TogglePattern)
                            if tog_pat and hasattr(tog_pat, "Toggle"):
                                tog_pat.Toggle()
                                invoke_result["success"] = True
                                invoke_result["method"] = "TogglePattern"
                                logger.info("Successfully toggled '%s' via UIA TogglePattern", el.name)
                                return
                        except Exception as exc:
                            logger.debug("TogglePattern failed for '%s': %s", el.name, exc)

                        # Attempt Pattern 3: SelectionItemPattern
                        try:
                            sel_pat = target_ctrl.GetPattern(auto.PatternId.SelectionItemPattern)
                            if sel_pat and hasattr(sel_pat, "Select"):
                                sel_pat.Select()
                                invoke_result["success"] = True
                                invoke_result["method"] = "SelectionItemPattern"
                                logger.info("Successfully selected '%s' via UIA SelectionItemPattern", el.name)
                                return
                        except Exception as exc:
                            logger.debug("SelectionItemPattern failed for '%s': %s", el.name, exc)

                        # Attempt Pattern 4: Direct UIA Control Click
                        try:
                            target_ctrl.Click()
                            invoke_result["success"] = True
                            invoke_result["method"] = "UIAClick"
                            logger.info("Successfully clicked '%s' via UIA Control.Click", el.name)
                            return
                        except Exception as exc:
                            logger.debug("Control.Click failed for '%s': %s", el.name, exc)
            except Exception as exc:
                logger.debug("Direct UIA worker failed for '%s': %s", el.name, exc)
                error_container.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=3.0)

        if invoke_result["success"]:
            return invoke_result

        # Fallback to coordinate-based click with strict cursor assertion
        from app.tools.desktop.mouse_keyboard import mouse_keyboard

        cx, cy = el.center
        logger.info("Direct UIA invoke unavailable for '%s', falling back to asserted coordinate click at (%d, %d)", el.name, cx, cy)
        assertion_data = mouse_keyboard.click_with_assertion(cx, cy, target_rect=el.rect)
        invoke_result["success"] = True
        invoke_result["method"] = "CoordinateFallback"
        invoke_result["assertion"] = assertion_data
        return invoke_result

    def click_element(self, element_id: str) -> dict[str, Any]:
        """Convenience alias for invoke_element."""
        return self.invoke_element(element_id)

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
