"""Comprehensive Test Suite for Hybrid Desktop Execution Architecture.

Covers:
1. Security Policy Guard (ReadOnly, LowRisk, HighRisk, Blocked).
2. Capability Resolver (Execution hierarchy, filesystem bypass, DOM, UIA, Win32).
3. Deterministic Desktop Verifier (Process, file, window state assertions, visual check).
4. State-Aware Recovery Ladder (Window refocus, alternative locators, keyboard shortcuts).
5. Optional CUA Background Driver.
6. Master DesktopExecutor End-to-End Pipeline & ExecutionTrace.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.execution.cua_backend import cua_backend
from app.execution.executor import DesktopExecutor, desktop_executor
from app.execution.models import (
    DesktopAction,
    DesktopTarget,
    ExecutionAttempt,
    ExecutionBackend,
    ExpectedState,
    Observation,
    VerificationResult,
)
from app.execution.policy import RiskTier, SecurityPolicyGuard, policy_guard
from app.execution.recovery import RecoveryManager, recovery_manager
from app.execution.resolver import CapabilityResolver, capability_resolver
from app.execution.verifier import DesktopVerifier, desktop_verifier


# ===========================================================================
# 1. Security Policy Guard Tests
# ===========================================================================

class TestSecurityPolicy:
    def test_read_only_classification(self):
        guard = SecurityPolicyGuard()
        action = DesktopAction(action_type="inspect_screen")
        tier, _ = guard.evaluate_action(action)
        assert tier == RiskTier.READ_ONLY

        action2 = DesktopAction(action_type="read_file", target=DesktopTarget(file_path="dummy.txt"))
        tier2, _ = guard.evaluate_action(action2)
        assert tier2 == RiskTier.READ_ONLY

    def test_blocks_critical_process_termination(self):
        guard = SecurityPolicyGuard()
        for proc in ("csrss.exe", "services.exe", "lsass.exe", "smss.exe", "wininit.exe"):
            action = DesktopAction(action_type="kill_process", target=DesktopTarget(process_name=proc))
            allowed, reason = guard.is_allowed(action)
            assert not allowed, f"Process {proc} should have been blocked!"
            assert "Blocked attempt to kill critical system process" in reason

    def test_blocks_dangerous_destructive_commands(self):
        guard = SecurityPolicyGuard()
        dangerous_cmds = [
            "format C: /fs:ntfs",
            "del /f /q C:\\",
            "rmdir /s /q C:\\",
            "diskpart",
        ]
        for cmd in dangerous_cmds:
            action = DesktopAction(action_type="terminal_command", params={"command": cmd})
            allowed, reason = guard.is_allowed(action)
            assert not allowed, f"Command '{cmd}' should have been blocked!"
            assert "Blocked dangerous command" in reason

    def test_blocks_protected_system_paths(self):
        guard = SecurityPolicyGuard()
        sys_path = os.environ.get("SystemRoot", r"C:\Windows")
        action = DesktopAction(action_type="delete_file", target=DesktopTarget(file_path=os.path.join(sys_path, "test.dll")))
        allowed, reason = guard.is_allowed(action)
        assert not allowed
        assert "Blocked attempt to delete system protected path" in reason

    def test_allows_safe_actions(self):
        guard = SecurityPolicyGuard()
        action = DesktopAction(action_type="launch_app", params={"app_name": "notepad"})
        allowed, _ = guard.is_allowed(action)
        assert allowed

        action2 = DesktopAction(action_type="click_element", target=DesktopTarget(element_id="elem_1"))
        allowed2, _ = guard.is_allowed(action2)
        assert allowed2


# ===========================================================================
# 2. Capability Resolver Tests
# ===========================================================================

class TestCapabilityResolver:
    def test_filesystem_bypass_over_gui(self):
        """Filesystem actions MUST route directly to OS_SYSTEM and never GUI clicks."""
        resolver = CapabilityResolver()
        action = DesktopAction(action_type="write_file", target=DesktopTarget(file_path="C:/test/file.txt"))
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.OS_SYSTEM
        assert len(plan.fallback_chain) == 0  # Deterministic OS API requires no GUI fallback
        assert "filesystem" in plan.rationale.lower()

    def test_terminal_resolves_to_os_system(self):
        resolver = CapabilityResolver()
        action = DesktopAction(action_type="terminal_command", params={"command": "dir"})
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.OS_SYSTEM

    def test_browser_dom_priority(self):
        """Browser targets MUST route to BROWSER_DOM."""
        resolver = CapabilityResolver()
        action = DesktopAction(
            action_type="click_element",
            target=DesktopTarget(dom_selector="#submit-button", process_name="chrome.exe"),
        )
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.BROWSER_DOM
        assert ExecutionBackend.UI_AUTOMATION in plan.fallback_chain

    def test_window_manipulation_priority(self):
        resolver = CapabilityResolver()
        focus_action = DesktopAction(action_type="focus_window", target=DesktopTarget(window_query="Notepad"))
        plan_focus = resolver.resolve(focus_action)
        assert plan_focus.primary_backend == ExecutionBackend.WIN32
        assert plan_focus.expected_state.window_state == "active"

        close_action = DesktopAction(action_type="close_window", target=DesktopTarget(window_query="Notepad"))
        plan_close = resolver.resolve(close_action)
        assert plan_close.primary_backend == ExecutionBackend.WIN32
        assert plan_close.expected_state.window_state == "closed"

    def test_ui_automation_element_priority(self):
        resolver = CapabilityResolver()
        click_action = DesktopAction(action_type="click_element", target=DesktopTarget(element_id="elem_4"))
        plan_click = resolver.resolve(click_action)
        assert plan_click.primary_backend == ExecutionBackend.UI_AUTOMATION
        assert ExecutionBackend.PYAUTOGUI in plan_click.fallback_chain


# ===========================================================================
# 3. Deterministic Desktop Verifier Tests
# ===========================================================================

class TestDesktopVerifier:
    def test_verify_file_assertions(self):
        verifier = DesktopVerifier()
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
            tmp.write("Voice Desktop Hybrid Execution Test")
            tmp_path = tmp.name

        try:
            # 1. File exists check
            exp_exists = ExpectedState(file_exists=tmp_path)
            res1 = verifier.verify(exp_exists, timeout=0.5)
            assert res1.verified

            # 2. Content check
            exp_content = ExpectedState(file_content_contains=(tmp_path, "Hybrid Execution"))
            res2 = verifier.verify(exp_content, timeout=0.5)
            assert res2.verified

            # 3. Content mismatch check
            exp_bad = ExpectedState(file_content_contains=(tmp_path, "NonExistentString123"))
            res3 = verifier.verify(exp_bad, timeout=0.2)
            assert not res3.verified

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # 4. File not exists check
        exp_not_exists = ExpectedState(file_not_exists=tmp_path)
        res4 = verifier.verify(exp_not_exists, timeout=0.5)
        assert res4.verified

    def test_verify_process_assertions(self):
        verifier = DesktopVerifier()
        # Current python process is running
        exp_running = ExpectedState(process_running="python")
        res1 = verifier.verify(exp_running, timeout=0.5)
        assert res1.verified

        # Nonexistent process is terminated
        exp_term = ExpectedState(process_terminated="completely_fake_process_999999.exe")
        res2 = verifier.verify(exp_term, timeout=0.5)
        assert res2.verified

    def test_visual_difference_computation(self):
        verifier = DesktopVerifier()
        img1 = Image.new("RGB", (200, 200), color=(0, 0, 0))
        img2 = Image.new("RGB", (200, 200), color=(0, 0, 0))
        changed, diff = verifier.verify_visual_difference(img1, img2)
        assert not changed
        assert diff == 0.0

        # Create significantly different image
        img3 = Image.new("RGB", (200, 200), color=(255, 255, 255))
        changed2, diff2 = verifier.verify_visual_difference(img1, img3)
        assert changed2
        assert diff2 > 0.5


# ===========================================================================
# 4. State-Aware Recovery Ladder Tests
# ===========================================================================

class TestRecoveryLadder:
    def test_recovers_unfocused_window(self):
        ladder = RecoveryManager()
        action = DesktopAction(action_type="click_element", target=DesktopTarget(window_query="Calculator"))
        plan = capability_resolver.resolve(action)

        with patch("app.tools.desktop.window_manager.window_manager.find_window") as mock_find, \
             patch("app.tools.desktop.window_manager.window_manager.is_window_active", return_value=False), \
             patch("app.tools.desktop.window_manager.window_manager.bring_to_front", return_value=True):

            mock_win = MagicMock(hwnd=1234, title="Calculator", is_minimized=False)
            mock_find.return_value = mock_win

            next_backend, ctx = ladder.attempt_recovery(
                action=action,
                plan=plan,
                failed_backend=ExecutionBackend.UI_AUTOMATION,
                error_message="Window unfocused",
            )
            assert ctx.get("strategy") == "restored_window_focus"
            assert next_backend == ExecutionBackend.UI_AUTOMATION

    def test_recovers_semantic_keyboard_alternatives(self):
        ladder = RecoveryManager()
        action_ok = DesktopAction(action_type="click_element", target=DesktopTarget(element_name="OK"))
        plan_ok = capability_resolver.resolve(action_ok)

        next_backend, ctx = ladder.attempt_recovery(
            action=action_ok,
            plan=plan_ok,
            failed_backend=ExecutionBackend.UI_AUTOMATION,
            error_message="Element not found",
        )
        assert next_backend == ExecutionBackend.KEYBOARD
        assert ctx.get("keys") == "enter"

        action_cancel = DesktopAction(action_type="click_element", target=DesktopTarget(element_name="Cancel"))
        plan_cancel = capability_resolver.resolve(action_cancel)

        next_backend2, ctx2 = ladder.attempt_recovery(
            action=action_cancel,
            plan=plan_cancel,
            failed_backend=ExecutionBackend.UI_AUTOMATION,
            error_message="Element not found",
        )
        assert next_backend2 == ExecutionBackend.KEYBOARD
        assert ctx2.get("keys") == "esc"

    def test_steps_down_fallback_ladder(self):
        ladder = RecoveryManager()
        action = DesktopAction(action_type="click_element", target=DesktopTarget(element_id="elem_9"))
        plan = capability_resolver.resolve(action)

        # Plan fallback chain: [CUA, PYAUTOGUI, VISION, COORDINATES]
        next_backend, ctx = ladder.attempt_recovery(
            action=action,
            plan=plan,
            failed_backend=ExecutionBackend.UI_AUTOMATION,
            error_message="UIA timed out",
        )
        assert next_backend == ExecutionBackend.CUA


# ===========================================================================
# 5. CUA Background Driver Tests
# ===========================================================================

class TestCuaBackend:
    def test_cua_availability(self):
        assert cua_backend.is_available()

    def test_invalid_hwnd_safely_handled(self):
        res = cua_backend.click_background(99999999, 100, 100)
        assert not res["success"]
        assert "Invalid HWND" in res["error"]


# ===========================================================================
# 6. Master DesktopExecutor End-to-End Tests
# ===========================================================================

class TestDesktopExecutorPipeline:
    def test_end_to_end_file_write_and_verify(self):
        """Execute filesystem write through DesktopExecutor with automatic verification & trace."""
        executor = DesktopExecutor()
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_file = os.path.join(tmp_dir, "executor_test.txt")
            action = DesktopAction(
                action_type="write_file",
                target=DesktopTarget(file_path=target_file),
                params={"content": "Verified by VOS Desktop Executor"},
            )

            result = asyncio.run(executor.execute(action))
            assert result.success
            assert result.backend_used == ExecutionBackend.OS_SYSTEM
            assert os.path.exists(target_file)
            assert len(result.attempts) == 1
            assert result.attempts[0].success
            assert any(t["event"] == "execution_success" for t in result.trace)

    def test_blocked_action_rejected_with_trace(self):
        executor = DesktopExecutor()
        action = DesktopAction(
            action_type="terminal_command",
            params={"command": "format C: /q"},
        )
        result = asyncio.run(executor.execute(action))
        assert not result.success
        assert "blocked by security policy" in result.message.lower()
        assert any(t["event"] == "policy_blocked" for t in result.trace)

    def test_recovery_trace_logging(self):
        """Verify that when a backend fails, the recovery ladder triggers and logs trace events."""
        executor = DesktopExecutor()
        action = DesktopAction(
            action_type="click_element",
            target=DesktopTarget(element_id="nonexistent_mock_elem_12345"),
            timeout=0.5,
        )

        result = asyncio.run(executor.execute(action))
        # Nonexistent element should fail gracefully after stepping down fallback ladder
        assert not result.success
        assert len(result.attempts) >= 1
        assert any(t["event"] == "attempt_start" for t in result.trace)


# ===========================================================================
# 7. Framework Scenarios & Multi-Window Ambiguity Tests
# ===========================================================================

class TestFrameworksAndScenarios:
    def test_notepad_plan_resolution(self):
        """Notepad input control targets UIA ValuePattern for deterministic text injection."""
        resolver = CapabilityResolver()
        action = DesktopAction(
            action_type="set_value",
            target=DesktopTarget(element_id="elem_edit", process_name="notepad.exe"),
            params={"value": "Hello world from VOS"},
        )
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.UI_AUTOMATION
        assert "valuepattern" in plan.rationale.lower()

    def test_explorer_filesystem_bypass(self):
        """File movement intent bypasses Explorer GUI completely."""
        resolver = CapabilityResolver()
        action = DesktopAction(
            action_type="write_file",
            target=DesktopTarget(file_path="C:/Users/Documents/report.docx"),
            params={"content": "data"},
        )
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.OS_SYSTEM
        assert len(plan.fallback_chain) == 0

    def test_settings_uwp_plan_resolution(self):
        """Windows Settings (systemsettings.exe) targets UI Automation."""
        resolver = CapabilityResolver()
        action = DesktopAction(
            action_type="click_element",
            target=DesktopTarget(element_name="Bluetooth & devices", process_name="systemsettings.exe"),
        )
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.UI_AUTOMATION

    def test_vscode_electron_plan_resolution(self):
        """VS Code file open action routes to terminal or direct file ops."""
        resolver = CapabilityResolver()
        action = DesktopAction(
            action_type="terminal_command",
            params={"command": "code main.py"},
        )
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.OS_SYSTEM

    def test_browser_dom_dom_prefix_routing(self):
        """Elements with dom_ prefix strictly route through BROWSER_DOM."""
        resolver = CapabilityResolver()
        action = DesktopAction(
            action_type="click_element",
            target=DesktopTarget(element_id="dom_42"),
        )
        plan = resolver.resolve(action)
        assert plan.primary_backend == ExecutionBackend.BROWSER_DOM

    def test_multi_window_ambiguity_targeting(self):
        """Window manager find_window_by_spec handles multiple windows with same process/title."""
        from app.tools.desktop.window_manager import WindowInfo, window_manager

        mock_windows = [
            WindowInfo(
                hwnd=1001,
                title="Untitled - Notepad",
                process_name="notepad.exe",
                pid=2001,
                rect=(0, 0, 500, 500),
                width=500,
                height=500,
                is_minimized=False,
                is_active=False,
                class_name="Notepad",
            ),
            WindowInfo(
                hwnd=1002,
                title="Notes.txt - Notepad",
                process_name="notepad.exe",
                pid=2002,
                rect=(100, 100, 600, 600),
                width=500,
                height=500,
                is_minimized=False,
                is_active=True,
                class_name="Notepad",
            ),
        ]

        with patch.object(window_manager, "list_windows", return_value=mock_windows):
            # Target by PID
            target_pid = window_manager.find_window_by_spec(pid=2002)
            assert target_pid is not None
            assert target_pid.hwnd == 1002

            # Target by HWND
            target_hwnd = window_manager.find_window_by_spec(hwnd=1001)
            assert target_hwnd is not None
            assert target_hwnd.title == "Untitled - Notepad"

            # Target by specific title substring with process filter
            target_title = window_manager.find_window_by_spec(process_name="notepad", title="Notes.txt")
            assert target_title is not None
            assert target_title.hwnd == 1002

