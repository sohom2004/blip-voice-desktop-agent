"""State-Aware Recovery Ladder for Desktop Automation.

Executes deterministic recovery strategies when primary execution or verification fails:
1. Re-assert target window focus and state (un-minimize, bring to top).
2. Detect blocking modal dialogs or popups.
3. Refresh accessibility tree and resolve alternative locators (Name -> AutomationId -> ControlType).
4. Keyboard shortcut equivalents (Enter, Esc, Ctrl+S, Alt+F4).
5. Background CUA / Win32 messaging.
6. Asserted coordinate PyAutoGUI mouse interaction.
7. Multimodal vision fallback.
8. Signal re-plan or escalation if unrecoverable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.execution.models import DesktopAction, ExecutionBackend, Observation
from app.execution.resolver import ExecutionPlan
from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.window_manager import window_manager

logger = logging.getLogger(__name__)


class RecoveryManager:
    """Coordinates state-aware recovery across the desktop automation hierarchy."""

    def attempt_recovery(
        self,
        action: DesktopAction,
        plan: ExecutionPlan,
        failed_backend: ExecutionBackend,
        error_message: str,
        observation: Observation | None = None,
    ) -> tuple[ExecutionBackend | None, dict[str, Any]]:
        """Determine next recovery step in the ladder.
        Returns (next_backend, recovery_context) or (None, failure_reason).
        """
        logger.warning(
            "Initiating recovery for action '%s' after failure in backend '%s': %s",
            action.action_type,
            failed_backend.value,
            error_message,
        )

        target = action.target

        # Step 1: Ensure Target Window is Still Active & Focused
        if target.hwnd or target.window_query:
            win = window_manager.find_window(target.hwnd or target.window_query)
            if win:
                if not window_manager.is_window_active(win.hwnd) or win.is_minimized:
                    logger.info("Recovery Step 1: Target window '%s' lost focus or is minimized; restoring...", win.title)
                    window_manager.bring_to_front(win.hwnd)
                    time.sleep(0.2)
                    # Retry with the same backend if window was just unfocused
                    return failed_backend, {"strategy": "restored_window_focus", "hwnd": win.hwnd}

        # Step 2: Modal Dialog Detection
        active = window_manager.get_active_window()
        if active and ("dialog" in active.class_name.lower() or "#32770" in active.class_name):
            logger.info("Recovery Step 2: Detected modal dialog '#32770' in foreground: '%s'", active.title)
            # Re-target active dialog
            return ExecutionBackend.UI_AUTOMATION, {"strategy": "modal_dialog_target", "hwnd": active.hwnd}

        # Step 3: UIA Element Alternative Locators
        if failed_backend == ExecutionBackend.UI_AUTOMATION and action.action_type in {"click_element", "set_value"}:
            if target.element_name or target.automation_id:
                active_hwnd = active.hwnd if active else (target.hwnd or 0)
                logger.info("Recovery Step 3: Refreshing UIA tree to search alternative locator for '%s'", target.element_name or target.automation_id)
                alt_elem = ui_inspector.find_element_by_criteria(
                    hwnd=active_hwnd,
                    name=target.element_name,
                    automation_id=target.automation_id,
                    control_type=target.control_type,
                )
                if alt_elem:
                    logger.info("Found alternative element '%s' (ID %s)", alt_elem.name, alt_elem.id)
                    target.element_id = alt_elem.id
                    target.coordinates = alt_elem.center
                    return ExecutionBackend.UI_AUTOMATION, {"strategy": "alternative_locator_found", "element_id": alt_elem.id}

        # Step 4: Keyboard Shortcut Alternatives for Common Semantic Actions
        if action.action_type == "click_element" and target.element_name:
            name_clean = target.element_name.strip().lower()
            if name_clean in {"ok", "submit", "confirm", "yes", "save"}:
                logger.info("Recovery Step 4: Attempting Keyboard Enter shortcut fallback for button '%s'", target.element_name)
                return ExecutionBackend.KEYBOARD, {"strategy": "keyboard_shortcut", "keys": "enter"}
            if name_clean in {"cancel", "close", "no", "dismiss"}:
                logger.info("Recovery Step 4: Attempting Keyboard Esc shortcut fallback for button '%s'", target.element_name)
                return ExecutionBackend.KEYBOARD, {"strategy": "keyboard_shortcut", "keys": "esc"}

        # Step 5: Progress along the resolver's predefined fallback chain
        if plan.fallback_chain:
            try:
                curr_idx = plan.fallback_chain.index(failed_backend)
                remaining = plan.fallback_chain[curr_idx + 1 :]
            except ValueError:
                remaining = plan.fallback_chain

            if remaining:
                next_backend = remaining[0]
                logger.info("Recovery Step 5: Stepping down fallback ladder to backend: '%s'", next_backend.value)
                return next_backend, {"strategy": "fallback_ladder", "next_backend": next_backend.value}

        # Step 6: Vision Fallback as ultimate automated tier
        if failed_backend != ExecutionBackend.VISION:
            logger.info("Recovery Step 6: Escalating to Vision / Framebuffer inspection.")
            return ExecutionBackend.VISION, {"strategy": "vision_fallback"}

        # Step 7: Exhausted
        logger.error("All recovery ladder steps exhausted for action '%s'. Re-planning required.", action.action_type)
        return None, {"strategy": "exhausted", "error": error_message}


# Global recovery manager singleton
recovery_manager = RecoveryManager()
