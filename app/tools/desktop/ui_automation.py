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
    class_name: str = ""
    value: str = ""
    toggle_state: int = 0  # 0: Off, 1: On, 2: Indeterminate
    is_selected: bool = False
    help_text: str = ""

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

                                    # Extract semantic attributes safely
                                    class_name = getattr(child, "ClassName", "") or ""
                                    val = ""
                                    try:
                                        val_pat = child.GetPattern(auto.PatternId.ValuePattern)
                                        if val_pat and hasattr(val_pat, "Value"):
                                            val = val_pat.Value or ""
                                    except Exception:
                                        pass

                                    toggle_state = 0
                                    try:
                                        tog_pat = child.GetPattern(auto.PatternId.TogglePattern)
                                        if tog_pat and hasattr(tog_pat, "ToggleState"):
                                            toggle_state = int(tog_pat.ToggleState)
                                    except Exception:
                                        pass

                                    is_selected = False
                                    try:
                                        sel_pat = child.GetPattern(auto.PatternId.SelectionItemPattern)
                                        if sel_pat and hasattr(sel_pat, "IsSelected"):
                                            is_selected = bool(sel_pat.IsSelected)
                                    except Exception:
                                        pass

                                    help_text = getattr(child, "HelpText", "") or ""

                                    elem = UIElement(
                                        id=f"elem_{count}",
                                        name=name,
                                        control_type=ctype.replace("Control", ""),
                                        automation_id=auto_id,
                                        rect=(rect.left, rect.top, rect.right, rect.bottom),
                                        center=(center_x, center_y),
                                        is_enabled=bool(child.IsEnabled),
                                        hwnd=hwnd,
                                        class_name=class_name,
                                        value=val,
                                        toggle_state=toggle_state,
                                        is_selected=is_selected,
                                        help_text=help_text,
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

    def _find_target_control(self, root: auto.Control, el: UIElement) -> auto.Control | None:
        """Resolve a UIElement to a live uiautomation Control."""
        # 1. Search by AutomationId if available
        if el.automation_id:
            target = root.FindControl(
                lambda c, d: c.AutomationId == el.automation_id and c.ControlTypeName.replace("Control", "") == el.control_type,
                maxDepth=8,
            )
            if target:
                return target

        # 2. Search by matching bounding box center
        target = root.FindControl(
            lambda c, d: (
                abs((c.BoundingRectangle.left + c.BoundingRectangle.right) // 2 - el.center[0]) <= 8
                and abs((c.BoundingRectangle.top + c.BoundingRectangle.bottom) // 2 - el.center[1]) <= 8
                and c.ControlTypeName.replace("Control", "") == el.control_type
            ),
            maxDepth=8,
        )
        if target:
            return target

        # 3. Search by exact name and control type
        if el.name:
            target = root.FindControl(
                lambda c, d: c.Name.strip() == el.name and c.ControlTypeName.replace("Control", "") == el.control_type,
                maxDepth=8,
            )
            if target:
                return target

        return None

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

                    target_ctrl = self._find_target_control(root, el)

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

    def set_value(self, element_id: str, value: str) -> dict[str, Any]:
        """Directly set text value using ValuePattern without typing keystrokes."""
        el = self._element_cache.get(element_id)
        if not el:
            return {"success": False, "error": f"Element '{element_id}' not found in cache."}

        res = {"success": False, "method": "none", "element": el.to_dict()}

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)

            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return
                    target_ctrl = self._find_target_control(root, el)
                    if target_ctrl:
                        try:
                            val_pat = target_ctrl.GetPattern(auto.PatternId.ValuePattern)
                            if val_pat and hasattr(val_pat, "SetValue"):
                                val_pat.SetValue(value)
                                res["success"] = True
                                res["method"] = "ValuePattern.SetValue"
                                el.value = value
                                logger.info("Successfully set value on '%s' via ValuePattern", el.name)
                                return
                        except Exception as exc:
                            logger.debug("ValuePattern.SetValue failed: %s", exc)

                        # Secondary attempt: focus control
                        try:
                            target_ctrl.SetFocus()
                        except Exception:
                            pass
            except Exception as exc:
                logger.debug("set_value worker error: %s", exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=3.0)

        if not res["success"]:
            # Fallback to mouse click and typing
            from app.tools.desktop.mouse_keyboard import mouse_keyboard
            cx, cy = el.center
            mouse_keyboard.click(cx, cy)
            mouse_keyboard.type_text(value)
            el.value = value
            res["success"] = True
            res["method"] = "ClickAndTypeFallback"

        return res

    def get_value(self, element_id: str) -> str | None:
        """Read the live value of an element using ValuePattern."""
        el = self._element_cache.get(element_id)
        if not el:
            return None

        val_holder = [None]

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)
            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return
                    target_ctrl = self._find_target_control(root, el)
                    if target_ctrl:
                        try:
                            val_pat = target_ctrl.GetPattern(auto.PatternId.ValuePattern)
                            if val_pat and hasattr(val_pat, "Value"):
                                val_holder[0] = val_pat.Value
                        except Exception:
                            pass
            except Exception:
                pass

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2.0)
        return val_holder[0] if val_holder[0] is not None else el.value

    def toggle_element(self, element_id: str) -> dict[str, Any]:
        """Toggle a checkbox or radio button via TogglePattern."""
        el = self._element_cache.get(element_id)
        if not el:
            return {"success": False, "error": f"Element '{element_id}' not found in cache."}

        res = {"success": False, "method": "none", "element": el.to_dict()}

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)
            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return
                    target_ctrl = self._find_target_control(root, el)
                    if target_ctrl:
                        try:
                            tog_pat = target_ctrl.GetPattern(auto.PatternId.TogglePattern)
                            if tog_pat and hasattr(tog_pat, "Toggle"):
                                tog_pat.Toggle()
                                res["success"] = True
                                res["method"] = "TogglePattern"
                                return
                        except Exception as exc:
                            logger.debug("TogglePattern error: %s", exc)
            except Exception:
                pass

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2.5)

        if not res["success"]:
            return self.invoke_element(element_id)
        return res

    def select_element(self, element_id: str) -> dict[str, Any]:
        """Select an item in a list, tab, or combo via SelectionItemPattern."""
        el = self._element_cache.get(element_id)
        if not el:
            return {"success": False, "error": f"Element '{element_id}' not found in cache."}

        res = {"success": False, "method": "none", "element": el.to_dict()}

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)
            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return
                    target_ctrl = self._find_target_control(root, el)
                    if target_ctrl:
                        try:
                            sel_pat = target_ctrl.GetPattern(auto.PatternId.SelectionItemPattern)
                            if sel_pat and hasattr(sel_pat, "Select"):
                                sel_pat.Select()
                                res["success"] = True
                                res["method"] = "SelectionItemPattern"
                                return
                        except Exception as exc:
                            logger.debug("SelectionItemPattern error: %s", exc)
            except Exception:
                pass

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2.5)

        if not res["success"]:
            return self.invoke_element(element_id)
        return res

    def expand_element(self, element_id: str, expand: bool = True) -> dict[str, Any]:
        """Expand or collapse a tree item or dropdown via ExpandCollapsePattern."""
        el = self._element_cache.get(element_id)
        if not el:
            return {"success": False, "error": f"Element '{element_id}' not found in cache."}

        res = {"success": False, "method": "none", "element": el.to_dict()}

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)
            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return
                    target_ctrl = self._find_target_control(root, el)
                    if target_ctrl:
                        try:
                            exp_pat = target_ctrl.GetPattern(auto.PatternId.ExpandCollapsePattern)
                            if exp_pat:
                                if expand and hasattr(exp_pat, "Expand"):
                                    exp_pat.Expand()
                                elif not expand and hasattr(exp_pat, "Collapse"):
                                    exp_pat.Collapse()
                                res["success"] = True
                                res["method"] = "ExpandCollapsePattern"
                                return
                        except Exception as exc:
                            logger.debug("ExpandCollapsePattern error: %s", exc)
            except Exception:
                pass

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2.5)

        if not res["success"]:
            return self.invoke_element(element_id)
        return res

    def scroll_element(self, element_id: str, direction: str = "down") -> dict[str, Any]:
        """Scroll container via ScrollPattern."""
        el = self._element_cache.get(element_id)
        if not el:
            return {"success": False, "error": f"Element '{element_id}' not found in cache."}

        res = {"success": False, "method": "none"}

        def worker():
            h_default = self._get_interactive_desktop_handle()
            if h_default:
                self.user32.SetThreadDesktop(h_default)
            try:
                with auto.UIAutomationInitializerInThread():
                    root = auto.ControlFromHandle(el.hwnd) if el.hwnd else auto.GetRootControl()
                    if not root:
                        return
                    target_ctrl = self._find_target_control(root, el)
                    if target_ctrl:
                        try:
                            scr_pat = target_ctrl.GetPattern(auto.PatternId.ScrollPattern)
                            if scr_pat and hasattr(scr_pat, "Scroll"):
                                h_amt = auto.ScrollAmount.NoAmount
                                v_amt = auto.ScrollAmount.SmallIncrement if direction == "down" else auto.ScrollAmount.SmallDecrement
                                scr_pat.Scroll(h_amt, v_amt)
                                res["success"] = True
                                res["method"] = "ScrollPattern"
                                return
                        except Exception as exc:
                            logger.debug("ScrollPattern error: %s", exc)
            except Exception:
                pass

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2.5)
        return res

    def find_element_by_criteria(
        self,
        hwnd: int,
        name: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        class_name: str | None = None,
    ) -> UIElement | None:
        """Find an element in window by name, automation ID, control type, or class name."""
        elements = self.inspect_window(hwnd, max_elements=60)
        for el in elements:
            if automation_id and el.automation_id == automation_id:
                return el
            if name and el.name.lower() == name.lower():
                return el
            if name and name.lower() in el.name.lower():
                if control_type and el.control_type.lower() != control_type.lower():
                    continue
                return el
            if control_type and el.control_type.lower() == control_type.lower():
                if not name and not automation_id:
                    return el
        return None

    def wait_for_element(
        self,
        hwnd: int,
        name: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        timeout: float = 5.0,
    ) -> UIElement | None:
        """Poll until matching element appears in window or timeout expires."""
        import time
        start_t = time.time()
        while time.time() - start_t < timeout:
            el = self.find_element_by_criteria(
                hwnd, name=name, automation_id=automation_id, control_type=control_type
            )
            if el:
                return el
            time.sleep(0.25)
        return None

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
