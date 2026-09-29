"""Master Desktop Executor.

Orchestrates the hybrid execution pipeline:
1. Security Policy Guard check.
2. Capability Resolver (optimal backend & fallback chain).
3. Backend Execution (OS -> DOM -> UIA -> Win32 -> Keyboard -> CUA -> PyAutoGUI -> Vision).
4. Observation capture.
5. Deterministic Verification assertions.
6. State-aware Recovery ladder if verification fails.
7. Complete audit trail & ExecutionTrace generation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from app.execution.cua_backend import cua_backend
from app.execution.models import (
    DesktopAction,
    DesktopTarget,
    ExecutionAttempt,
    ExecutionBackend,
    ExecutionResult,
    ExpectedState,
    Observation,
    VerificationResult,
)
from app.execution.policy import policy_guard
from app.execution.recovery import recovery_manager
from app.execution.resolver import capability_resolver
from app.execution.verifier import desktop_verifier
from app.tools.browser.browser_manager import browser_manager
from app.tools.desktop.mouse_keyboard import mouse_keyboard
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.window_manager import window_manager
from app.tools.system.file_ops import file_manager
from app.tools.system.terminal import terminal_manager

logger = logging.getLogger(__name__)


class DesktopExecutor:
    """Master controller for hybrid desktop automation."""

    def __init__(self):
        self.resolver = capability_resolver
        self.verifier = desktop_verifier
        self.recovery = recovery_manager
        self.policy = policy_guard

    async def execute(self, action: DesktopAction) -> ExecutionResult:
        """Execute a desktop action through the resilient hybrid pipeline."""
        start_time = time.perf_counter()
        trace: list[dict[str, Any]] = []

        def log_trace(event: str, data: dict[str, Any] | None = None):
            trace.append({"event": event, "timestamp": time.time(), "data": data or {}})

        log_trace("action_received", action.to_dict())

        # Step 1: Security Policy Guardrail
        allowed, policy_msg = self.policy.is_allowed(action)
        if not allowed:
            log_trace("policy_blocked", {"reason": policy_msg})
            return ExecutionResult(
                success=False,
                action_type=action.action_type,
                backend_used=None,
                message=f"Action blocked by security policy: {policy_msg}",
                duration_seconds=round(time.perf_counter() - start_time, 3),
                trace=trace,
            )

        # Step 2: Capability Resolution
        plan = self.resolver.resolve(action)
        log_trace("plan_resolved", plan.to_dict())

        current_backend: ExecutionBackend | None = plan.primary_backend
        attempts: list[ExecutionAttempt] = []
        max_attempts = 4
        attempt_count = 0
        final_observation: Observation | None = None

        while current_backend and attempt_count < max_attempts:
            attempt_count += 1
            attempt_start = time.perf_counter()
            log_trace("attempt_start", {"backend": current_backend.value, "attempt": attempt_count})

            # Execute with current backend
            backend_res = await self._dispatch_backend(current_backend, action, plan)
            exec_duration = round(time.perf_counter() - attempt_start, 3)

            # Observe post-action desktop state
            final_observation = await self.observe(action.target)
            if "visual_diff_ratio" in backend_res:
                final_observation.visual_diff_ratio = backend_res["visual_diff_ratio"]

            # Deterministic Verification
            verification: VerificationResult
            if backend_res.get("success"):
                verification = self.verifier.verify(
                    plan.expected_state,
                    observation=final_observation,
                    timeout=action.timeout if attempt_count == 1 else 1.5,
                )
            else:
                verification = VerificationResult(
                    verified=False,
                    check_type="backend_error",
                    message=backend_res.get("error", "Backend execution failed."),
                )

            attempt = ExecutionAttempt(
                backend=current_backend,
                timestamp=time.time(),
                success=verification.verified,
                message=verification.message,
                verification=verification,
                duration_seconds=exec_duration,
            )
            attempts.append(attempt)
            log_trace("attempt_result", attempt.to_dict())

            if verification.verified:
                total_duration = round(time.perf_counter() - start_time, 3)
                log_trace("execution_success", {"backend": current_backend.value, "duration": total_duration})
                return ExecutionResult(
                    success=True,
                    action_type=action.action_type,
                    backend_used=current_backend,
                    message=f"Action '{action.action_type}' succeeded via {current_backend.value}: {verification.message}",
                    attempts=attempts,
                    final_observation=final_observation,
                    duration_seconds=total_duration,
                    trace=trace,
                )

            # Step 3: Trigger Recovery if verification failed
            if not action.allow_fallback:
                break

            next_backend, recovery_ctx = self.recovery.attempt_recovery(
                action=action,
                plan=plan,
                failed_backend=current_backend,
                error_message=verification.message,
                observation=final_observation,
            )
            log_trace("recovery_eval", {"next_backend": next_backend.value if next_backend else None, "context": recovery_ctx})

            if not next_backend or next_backend == current_backend:
                # If recovery suggests same backend (e.g. after restoring window focus), retry once
                if recovery_ctx.get("strategy") == "restored_window_focus" and attempt_count < 2:
                    continue
                break

            current_backend = next_backend

        # All attempts failed
        total_duration = round(time.perf_counter() - start_time, 3)
        failure_msg = attempts[-1].message if attempts else "Execution failed with no attempts."
        log_trace("execution_failed", {"duration": total_duration, "message": failure_msg})

        return ExecutionResult(
            success=False,
            action_type=action.action_type,
            backend_used=attempts[-1].backend if attempts else None,
            message=f"Action '{action.action_type}' failed after {len(attempts)} attempts: {failure_msg}",
            attempts=attempts,
            final_observation=final_observation,
            duration_seconds=total_duration,
            trace=trace,
        )

    async def _dispatch_backend(
        self,
        backend: ExecutionBackend,
        action: DesktopAction,
        plan: Any,
    ) -> dict[str, Any]:
        """Dispatch action to specific automation backend implementation."""
        atype = action.action_type.lower()
        target = action.target
        params = action.params

        try:
            # 1. OS_SYSTEM Backend
            if backend == ExecutionBackend.OS_SYSTEM:
                if atype == "launch_app":
                    app_name = str(params.get("app_name", target.window_query or ""))
                    ok = window_manager.launch_app(app_name, wait_for_window=True)
                    return {"success": ok}

                if atype in {"read_file", "write_file", "patch_file", "list_dir", "search_files"}:
                    return self._dispatch_file_op(atype, target, params)

                if atype == "delete_file":
                    p = Path(target.file_path or params.get("file_path", "")).resolve()
                    if p.is_file():
                        p.unlink()
                        return {"success": True, "file_path": str(p)}
                    return {"success": False, "error": f"File not found: {p}"}

                if atype in {"terminal_command", "execute_terminal", "run_terminal"}:
                    cmd = str(params.get("command", ""))
                    res = await terminal_manager.execute(cmd, timeout=action.timeout)
                    return {"success": res.exit_code == 0, "exit_code": res.exit_code, "output": res.output}

            # 2. BROWSER_DOM Backend
            if backend == ExecutionBackend.BROWSER_DOM:
                if atype in {"click_element", "browser_click"}:
                    elem_id = target.dom_selector or target.element_id or ""
                    res = browser_manager.click_element_sync(elem_id)
                    return res

                if atype in {"type_text", "set_value", "browser_type"}:
                    elem_id = target.dom_selector or target.element_id or ""
                    text = str(params.get("text", params.get("value", "")))
                    res = browser_manager.fill_element_sync(elem_id, text)
                    return res

                if atype in {"open_web", "browser_navigate"}:
                    url = str(params.get("url", target.window_query or ""))
                    res = browser_manager.open_url_sync(url)
                    return res

                if atype == "scroll_web_page":
                    direction = str(params.get("direction", "down"))
                    pixels = int(params.get("pixels", 500))
                    res = browser_manager.scroll_page_sync(direction=direction, pixels=pixels)
                    return res

            # 3. UI_AUTOMATION Backend
            if backend == ExecutionBackend.UI_AUTOMATION:
                if atype == "click_element":
                    elem_id = target.element_id
                    if not elem_id and (target.element_name or target.automation_id):
                        active_w = window_manager.get_active_window()
                        h = active_w.hwnd if active_w else (target.hwnd or 0)
                        found = ui_inspector.find_element_by_criteria(
                            hwnd=h,
                            name=target.element_name,
                            automation_id=target.automation_id,
                            control_type=target.control_type,
                        )
                        if found:
                            elem_id = found.id
                    if not elem_id:
                        return {"success": False, "error": "Target UI element ID not resolved."}
                    return ui_inspector.invoke_element(elem_id)

                if atype == "set_value":
                    elem_id = target.element_id
                    val = str(params.get("value", params.get("text", "")))
                    if not elem_id:
                        return {"success": False, "error": "Target element ID missing for set_value."}
                    return ui_inspector.set_value(elem_id, val)

                if atype == "toggle":
                    elem_id = target.element_id or ""
                    return ui_inspector.toggle_element(elem_id)

                if atype == "select":
                    elem_id = target.element_id or ""
                    return ui_inspector.select_element(elem_id)

                if atype == "scroll":
                    elem_id = target.element_id or ""
                    direction = str(params.get("direction", "down"))
                    if elem_id:
                        return ui_inspector.scroll_element(elem_id, direction)
                    mouse_keyboard.scroll(clicks=5, direction=direction)
                    return {"success": True}

            # 4. WIN32 Backend
            if backend == ExecutionBackend.WIN32:
                q = target.hwnd or target.window_query or params.get("query", "")
                if atype == "focus_window":
                    ok = window_manager.bring_to_front(q)
                    return {"success": ok}
                if atype == "close_window":
                    ok = window_manager.close_window(q)
                    return {"success": ok}
                if atype == "minimize_window":
                    ok = window_manager.minimize_window(q)
                    return {"success": ok}
                if atype == "maximize_window":
                    ok = window_manager.maximize_window(q)
                    return {"success": ok}

            # 5. KEYBOARD Backend
            if backend == ExecutionBackend.KEYBOARD:
                if atype in {"keyboard_shortcut", "hotkey"}:
                    keys = str(params.get("keys", "")).split("+")
                    key_list = [k.strip().lower() for k in keys if k.strip()]
                    mouse_keyboard.hotkey(*key_list)
                    return {"success": True, "keys": key_list}

                if atype in {"type_text", "set_value"}:
                    text = str(params.get("text", params.get("value", "")))
                    mouse_keyboard.type_text(text)
                    return {"success": True, "typed": text}

                if atype == "click_element":
                    # Fallback keypress (e.g. Enter / Esc)
                    key = str(params.get("key", "enter"))
                    mouse_keyboard.press_key(key)
                    return {"success": True, "key": key}

            # 6. CUA Backend (Background Win32 message queue)
            if backend == ExecutionBackend.CUA:
                hwnd = target.hwnd
                if not hwnd and target.window_query:
                    win = window_manager.find_window(target.window_query)
                    hwnd = win.hwnd if win else None

                if not hwnd:
                    active = window_manager.get_active_window()
                    hwnd = active.hwnd if active else None

                if hwnd and atype == "click_element" and target.coordinates:
                    cx, cy = target.coordinates
                    return cua_backend.click_background(hwnd, cx, cy)

                if hwnd and atype in {"type_text", "set_value"}:
                    text = str(params.get("text", params.get("value", "")))
                    return cua_backend.type_background(hwnd, text)

                return {"success": False, "error": "CUA backend requires valid target HWND."}

            # 7. PYAUTOGUI Backend (Asserted coordinates)
            if backend == ExecutionBackend.PYAUTOGUI:
                if target.coordinates:
                    cx, cy = target.coordinates
                    mouse_keyboard.click(cx, cy)
                    return {"success": True, "coords": (cx, cy)}

                if target.element_id:
                    elem = ui_inspector.get_element(target.element_id)
                    if elem:
                        cx, cy = elem.center
                        mouse_keyboard.click(cx, cy)
                        return {"success": True, "coords": (cx, cy)}
                    return {"success": False, "error": f"No coordinates found for element '{target.element_id}'."}

                active = window_manager.get_active_window()
                if active:
                    cx = active.rect[0] + active.width // 2
                    cy = active.rect[1] + active.height // 2
                    mouse_keyboard.click(cx, cy)
                    return {"success": True, "coords": (cx, cy)}

                return {"success": False, "error": "No valid coordinates for PyAutoGUI click."}

            # 8. VISION Backend (MSS Framebuffer)
            if backend == ExecutionBackend.VISION:
                img = screen_capture.capture_primary_monitor()
                if atype in {"inspect", "inspect_screen", "screen_capture", "observe"}:
                    return {"success": True, "visual_captured": True, "image_size": img.size}
                return {
                    "success": False,
                    "error": f"Visual grounding required for '{atype}'. Target not directly resolvable.",
                    "requires_escalation": True,
                }

            return {"success": False, "error": f"Unsupported backend '{backend.value}' for action '{atype}'."}

        except Exception as exc:
            logger.error("Backend %s raised exception on %s: %s", backend.value, atype, exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def _dispatch_file_op(self, atype: str, target: DesktopTarget, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch filesystem operations to FileManager."""
        path = target.file_path or params.get("file_path", "")
        if atype == "read_file":
            return file_manager.read_file(
                path,
                start_line=params.get("start_line", 1),
                end_line=params.get("end_line"),
            )
        if atype == "write_file":
            return file_manager.write_file(
                path,
                content=params.get("content", ""),
                overwrite=params.get("overwrite", True),
            )
        if atype == "patch_file":
            return file_manager.patch_file(
                path,
                target_content=params.get("target_content", ""),
                replacement_content=params.get("replacement_content", ""),
            )
        if atype == "list_dir":
            return file_manager.list_dir(
                dir_path=path or ".",
                pattern=params.get("pattern", "*"),
                recursive=params.get("recursive", False),
            )
        if atype == "search_files":
            return file_manager.search_in_files(
                query=params.get("query", ""),
                dir_path=path or ".",
                file_pattern=params.get("pattern", "*.py"),
            )
        return {"success": False, "error": f"Unknown file operation: {atype}"}

    async def observe(self, target: DesktopTarget | None = None) -> Observation:
        """Capture the live desktop observation."""
        active = window_manager.get_active_window()
        open_wins = window_manager.list_windows()

        element_val = None
        if target and target.element_id:
            element_val = ui_inspector.get_value(target.element_id)

        return Observation(
            active_window=active.to_dict() if active else None,
            open_windows_count=len(open_wins),
            element_value=element_val,
        )

    def execute_sync(self, action: DesktopAction) -> ExecutionResult:
        """Synchronous wrapper for desktop execution."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.execute(action)).result()
        return loop.run_until_complete(self.execute(action))


# Global DesktopExecutor singleton
desktop_executor = DesktopExecutor()
