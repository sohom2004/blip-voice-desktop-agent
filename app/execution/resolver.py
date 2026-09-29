"""Capability Resolver for Desktop Automation.

Determines the fastest, most deterministic execution strategy for each action,
enforcing the execution hierarchy:
1. Native application API / CLI
2. OS / System API (filesystem, terminal, process)
3. Browser DOM (Playwright / CDP)
4. Windows UI Automation (ValuePattern, InvokePattern, etc.)
5. Win32-specific window mechanisms
6. Keyboard shortcuts
7. CUA background driver (optional)
8. PyAutoGUI (asserted cursor movement / clicks)
9. Vision / Screen capture analysis (MSS)
10. Coordinates (last-resort fallback)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.execution.models import DesktopAction, DesktopTarget, ExecutionBackend, ExpectedState
from app.tools.desktop.window_manager import window_manager

logger = logging.getLogger(__name__)

# Application framework classifications
BROWSER_PROCESSES = {"chrome.exe", "brave.exe", "msedge.exe", "firefox.exe"}
ELECTRON_PROCESSES = {"code.exe", "slack.exe", "discord.exe", "teams.exe", "notion.exe", "spotify.exe"}
UWP_WINUI_PROCESSES = {"applicationframehost.exe", "calculatorapp.exe", "systemsettings.exe", "notepad.exe"}


@dataclass
class ExecutionPlan:
    action: DesktopAction
    primary_backend: ExecutionBackend
    fallback_chain: list[ExecutionBackend]
    expected_state: ExpectedState | None
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "primary_backend": self.primary_backend.value,
            "fallback_chain": [b.value for b in self.fallback_chain],
            "expected_state": self.expected_state.to_dict() if self.expected_state else None,
            "rationale": self.rationale,
        }


class CapabilityResolver:
    """Resolves actions to optimal execution strategies and deterministic fallbacks."""

    def resolve(self, action: DesktopAction) -> ExecutionPlan:
        """Analyze action and target context to produce an ExecutionPlan."""
        atype = action.action_type.lower()
        target = action.target

        # 1. Override if action explicitly specified a preferred backend
        if action.preferred_backend:
            return ExecutionPlan(
                action=action,
                primary_backend=action.preferred_backend,
                fallback_chain=self._get_default_fallbacks(action.preferred_backend),
                expected_state=action.expected_state or self._infer_expected_state(action),
                rationale=f"Explicitly requested backend '{action.preferred_backend.value}'.",
            )

        # 2. Filesystem operations -> OS/System API (NEVER GUI drag/drop)
        if atype in {"read_file", "write_file", "patch_file", "list_dir", "search_files", "delete_file", "move_file", "copy_file"}:
            exp = action.expected_state or self._infer_expected_state(action)
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.OS_SYSTEM,
                fallback_chain=[],  # Deterministic OS APIs do not need GUI fallback
                expected_state=exp,
                rationale="Direct filesystem OS API is deterministic and faster than GUI manipulation.",
            )

        # 3. Terminal & process operations -> OS/System API
        if atype in {"terminal_command", "execute_terminal", "run_terminal", "kill_process", "check_process"}:
            exp = action.expected_state or self._infer_expected_state(action)
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.OS_SYSTEM,
                fallback_chain=[],
                expected_state=exp,
                rationale="System process / terminal harness provides direct asynchronous execution.",
            )

        # 4. Web browser operations -> Browser DOM (Playwright/CDP)
        is_browser_target = self._is_browser_target(target)
        if is_browser_target or atype in {"browser_click", "browser_type", "browser_navigate", "scroll_web_page"}:
            exp = action.expected_state or self._infer_expected_state(action)
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.BROWSER_DOM,
                fallback_chain=[
                    ExecutionBackend.UI_AUTOMATION,
                    ExecutionBackend.PYAUTOGUI,
                    ExecutionBackend.VISION,
                ],
                expected_state=exp,
                rationale="Browser DOM provides direct CDP/Playwright inspection without cursor displacement.",
            )

        # 5. Window level manipulations (Focus, Close, Minimize, Maximize)
        if atype == "focus_window":
            exp = action.expected_state or ExpectedState(window_state="active")
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.WIN32,
                fallback_chain=[
                    ExecutionBackend.UI_AUTOMATION,
                    ExecutionBackend.KEYBOARD,  # Alt+Tab
                ],
                expected_state=exp,
                rationale="Win32 SetForegroundWindow with AttachThreadInput provides direct foreground focus.",
            )

        if atype == "close_window":
            exp = action.expected_state or ExpectedState(window_state="closed")
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.WIN32,
                fallback_chain=[
                    ExecutionBackend.KEYBOARD,  # Alt+F4
                    ExecutionBackend.UI_AUTOMATION,
                ],
                expected_state=exp,
                rationale="Win32 WM_CLOSE is standard and graceful; Alt+F4 as keyboard fallback.",
            )

        if atype in {"minimize_window", "maximize_window"}:
            state_needed = "minimized" if atype == "minimize_window" else "visible"
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.WIN32,
                fallback_chain=[ExecutionBackend.KEYBOARD],
                expected_state=ExpectedState(window_state=state_needed),
                rationale="Win32 ShowWindow(SW_MINIMIZE/SW_MAXIMIZE) directly manipulates window state.",
            )

        if atype == "launch_app":
            app_name = str(action.params.get("app_name", target.window_query or "")).strip()
            exp = action.expected_state or ExpectedState(
                window_state="active",
                window_title_contains=app_name if app_name else None,
            )
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.OS_SYSTEM,
                fallback_chain=[ExecutionBackend.WIN32, ExecutionBackend.KEYBOARD],
                expected_state=exp,
                rationale="System shell process spawn is fast and reliable for app launching.",
            )

        # 6. Keyboard shortcuts & Hotkeys
        if atype in {"keyboard_shortcut", "hotkey"}:
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.KEYBOARD,
                fallback_chain=[],
                expected_state=action.expected_state,
                rationale="Direct simulated keypress combo via PyAutoGUI/Win32 keybd_event.",
            )

        # 7. UI Controls: Click Element
        if atype == "click_element":
            exp = action.expected_state or self._infer_expected_state(action)
            # Prioritize Windows UI Automation patterns
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.UI_AUTOMATION,
                fallback_chain=[
                    ExecutionBackend.CUA,
                    ExecutionBackend.PYAUTOGUI,
                    ExecutionBackend.VISION,
                    ExecutionBackend.COORDINATES,
                ],
                expected_state=exp,
                rationale="Windows UI Automation (InvokePattern/TogglePattern) clicks semantically without cursor hijacking.",
            )

        # 8. UI Controls: Set Value / Type Text
        if atype in {"set_value", "type_text"}:
            exp = action.expected_state or self._infer_expected_state(action)
            # If set_value or target has element_id, try UIA ValuePattern first
            if target.element_id or atype == "set_value":
                return ExecutionPlan(
                    action=action,
                    primary_backend=ExecutionBackend.UI_AUTOMATION,
                    fallback_chain=[
                        ExecutionBackend.KEYBOARD,
                        ExecutionBackend.PYAUTOGUI,
                    ],
                    expected_state=exp,
                    rationale="UIA ValuePattern.SetValue sets text instantly without typing or clipboard corruption.",
                )
            else:
                return ExecutionPlan(
                    action=action,
                    primary_backend=ExecutionBackend.KEYBOARD,
                    fallback_chain=[ExecutionBackend.PYAUTOGUI],
                    expected_state=exp,
                    rationale="Simulated keyboard keystrokes for active focus control.",
                )

        # 9. UI Controls: Scroll
        if atype == "scroll":
            return ExecutionPlan(
                action=action,
                primary_backend=ExecutionBackend.UI_AUTOMATION,
                fallback_chain=[
                    ExecutionBackend.PYAUTOGUI,
                    ExecutionBackend.KEYBOARD,  # PageUp / PageDown
                ],
                expected_state=action.expected_state,
                rationale="UIA ScrollPattern provides deterministic viewport scrolling; mouse wheel as fallback.",
            )

        # Default fallback plan
        return ExecutionPlan(
            action=action,
            primary_backend=ExecutionBackend.UI_AUTOMATION,
            fallback_chain=[
                ExecutionBackend.WIN32,
                ExecutionBackend.KEYBOARD,
                ExecutionBackend.PYAUTOGUI,
            ],
            expected_state=action.expected_state,
            rationale="Default UIA strategy with multi-tier fallback ladder.",
        )

    def _is_browser_target(self, target: DesktopTarget) -> bool:
        """Check if target represents a web browser application or page."""
        if target.dom_selector or (target.element_id and target.element_id.startswith("dom_")):
            return True

        if target.process_name:
            p = target.process_name.lower()
            if not p.endswith(".exe"):
                p += ".exe"
            if p in BROWSER_PROCESSES:
                return True

        if target.hwnd:
            active = window_manager.get_active_window()
            if active and active.hwnd == target.hwnd:
                if active.process_name.lower() in BROWSER_PROCESSES:
                    return True

        # Check currently active window
        active = window_manager.get_active_window()
        if active and active.process_name.lower() in BROWSER_PROCESSES and not target.window_query:
            return True

        return False

    def _get_default_fallbacks(self, primary: ExecutionBackend) -> list[ExecutionBackend]:
        """Construct a descending fallback ladder below the specified primary backend."""
        order = [
            ExecutionBackend.NATIVE_API,
            ExecutionBackend.OS_SYSTEM,
            ExecutionBackend.BROWSER_DOM,
            ExecutionBackend.UI_AUTOMATION,
            ExecutionBackend.WIN32,
            ExecutionBackend.KEYBOARD,
            ExecutionBackend.CUA,
            ExecutionBackend.PYAUTOGUI,
            ExecutionBackend.VISION,
            ExecutionBackend.COORDINATES,
        ]
        try:
            idx = order.index(primary)
            return order[idx + 1 :]
        except ValueError:
            return []

    def _infer_expected_state(self, action: DesktopAction) -> ExpectedState | None:
        """Derive postcondition assertion criteria from action parameters."""
        atype = action.action_type.lower()
        target = action.target

        if atype == "write_file":
            path = target.file_path or action.params.get("file_path")
            if path:
                return ExpectedState(file_exists=str(path))

        if atype == "delete_file":
            path = target.file_path or action.params.get("file_path")
            if path:
                return ExpectedState(file_not_exists=str(path))

        if atype == "patch_file":
            path = target.file_path or action.params.get("file_path")
            replacement = action.params.get("replacement_content")
            if path and replacement:
                return ExpectedState(file_content_contains=(str(path), replacement))

        if atype == "kill_process":
            proc = target.process_name or action.params.get("process")
            if proc:
                return ExpectedState(process_terminated=str(proc))

        if atype == "focus_window":
            return ExpectedState(window_state="active")

        if atype == "close_window":
            return ExpectedState(window_state="closed")

        return None


# Global resolver singleton
capability_resolver = CapabilityResolver()
