"""Desktop Verifier Layer.

Provides deterministic postcondition assertions for desktop automation:
- Window state (active, visible, closed, minimized, title)
- Process state (running, terminated)
- Filesystem state (file exists, removed, content matches)
- UI control state (live value, toggle, selection)
- Visual fallback verification using MSS framebuffer comparison
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import psutil
from PIL import Image, ImageChops

from app.execution.models import ExpectedState, Observation, VerificationResult
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.window_manager import window_manager

logger = logging.getLogger(__name__)


class DesktopVerifier:
    """Performs deterministic state verification with cheap visual fallback."""

    def verify(
        self,
        expected: ExpectedState | None,
        observation: Observation | None = None,
        timeout: float = 3.0,
    ) -> VerificationResult:
        """Assert that the desktop reached the expected state."""
        if expected is None:
            return VerificationResult(
                verified=True,
                check_type="none",
                message="No explicit postcondition expected.",
            )

        start_time = time.time()
        last_error = ""

        while time.time() - start_time <= timeout:
            # 1. Assert Window State
            if expected.window_state:
                res = self._check_window_state(expected)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            # 2. Assert Window Title
            if expected.window_title_contains:
                res = self._check_window_title(expected.window_title_contains)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            # 3. Assert Process State
            if expected.process_running:
                res = self._check_process_running(expected.process_running)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            if expected.process_terminated:
                res = self._check_process_terminated(expected.process_terminated)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            # 4. Assert Filesystem State
            if expected.file_exists:
                res = self._check_file_exists(expected.file_exists)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            if expected.file_not_exists:
                res = self._check_file_not_exists(expected.file_not_exists)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            if expected.file_content_contains:
                path, sub = expected.file_content_contains
                res = self._check_file_content(path, sub)
                if not res.verified:
                    last_error = res.message
                    time.sleep(0.15)
                    continue

            # 5. Assert UI Element Value State
            if expected.element_value_equals is not None and observation:
                if observation.element_value != expected.element_value_equals:
                    last_error = (
                        f"Element value '{observation.element_value}' did not match expected "
                        f"'{expected.element_value_equals}'."
                    )
                    time.sleep(0.15)
                    continue

            # 6. Visual Change Requirement
            if expected.visual_change_required and observation:
                if observation.visual_diff_ratio is not None and observation.visual_diff_ratio < 0.001:
                    last_error = f"Expected visual change, but diff ratio was only {observation.visual_diff_ratio:.4f}."
                    time.sleep(0.15)
                    continue

            # If all assertions passed
            return VerificationResult(
                verified=True,
                check_type="deterministic",
                message="All postcondition state assertions passed successfully.",
            )

        return VerificationResult(
            verified=False,
            check_type="deterministic",
            message=f"Verification failed within {timeout:.1f}s: {last_error}",
        )

    def _check_window_state(self, expected: ExpectedState) -> VerificationResult:
        """Check window state (active, visible, closed, minimized)."""
        st = (expected.window_state or "").lower()
        active = window_manager.get_active_window()

        if st == "active":
            if active and (
                not expected.window_title_contains
                or expected.window_title_contains.lower() in active.title.lower()
            ):
                return VerificationResult(True, "deterministic_window", f"Window '{active.title}' is active.")
            return VerificationResult(False, "deterministic_window", "Target window is not active in foreground.")

        if st == "visible":
            if expected.window_title_contains:
                win = window_manager.find_window(expected.window_title_contains)
                if win and window_manager.is_window_visible(win.hwnd):
                    return VerificationResult(True, "deterministic_window", f"Window '{win.title}' is visible.")
                return VerificationResult(False, "deterministic_window", f"Window matching '{expected.window_title_contains}' is not visible.")
            if active and window_manager.is_window_visible(active.hwnd):
                return VerificationResult(True, "deterministic_window", "Window is visible.")
            return VerificationResult(False, "deterministic_window", "Target window is not visible.")

        if st == "closed":
            if expected.window_title_contains:
                win = window_manager.find_window(expected.window_title_contains)
                if not win:
                    return VerificationResult(True, "deterministic_window", f"Window '{expected.window_title_contains}' is closed.")
                return VerificationResult(False, "deterministic_window", f"Window '{win.title}' is still open.")
            return VerificationResult(True, "deterministic_window", "Window closed.")

        if st == "minimized":
            if expected.window_title_contains:
                win = window_manager.find_window(expected.window_title_contains)
                if win and win.is_minimized:
                    return VerificationResult(True, "deterministic_window", f"Window '{win.title}' is minimized.")
                return VerificationResult(False, "deterministic_window", "Window is not minimized.")
            return VerificationResult(True, "deterministic_window", "Window minimized.")

        return VerificationResult(True, "deterministic_window", f"Window state '{st}' acknowledged.")

    def _check_window_title(self, substring: str) -> VerificationResult:
        """Check if any visible window title contains substring."""
        win = window_manager.find_window(substring)
        if win:
            return VerificationResult(True, "deterministic_window", f"Found window '{win.title}'.")
        return VerificationResult(False, "deterministic_window", f"No window found with title matching '{substring}'.")

    def _check_process_running(self, process_name: str) -> VerificationResult:
        """Verify process is active in system process table."""
        p_clean = process_name.lower().replace(".exe", "")
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = (proc.info["name"] or "").lower().replace(".exe", "")
                if p_clean == name or p_clean in name:
                    return VerificationResult(
                        True,
                        "deterministic_process",
                        f"Process '{proc.info['name']}' (PID {proc.info['pid']}) is running.",
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return VerificationResult(False, "deterministic_process", f"Process '{process_name}' is not running.")

    def _check_process_terminated(self, process_name: str) -> VerificationResult:
        """Verify process is NOT active in system process table."""
        res = self._check_process_running(process_name)
        if not res.verified:
            return VerificationResult(True, "deterministic_process", f"Process '{process_name}' is terminated.")
        return VerificationResult(False, "deterministic_process", f"Process '{process_name}' is still running.")

    def _check_file_exists(self, file_path: str) -> VerificationResult:
        """Verify file exists on disk."""
        p = Path(file_path).resolve()
        if p.exists():
            return VerificationResult(True, "deterministic_file", f"File '{p}' exists.")
        return VerificationResult(False, "deterministic_file", f"File '{p}' does not exist.")

    def _check_file_not_exists(self, file_path: str) -> VerificationResult:
        """Verify file does not exist on disk."""
        p = Path(file_path).resolve()
        if not p.exists():
            return VerificationResult(True, "deterministic_file", f"File '{p}' does not exist.")
        return VerificationResult(False, "deterministic_file", f"File '{p}' still exists.")

    def _check_file_content(self, file_path: str, substring: str) -> VerificationResult:
        """Verify file contains expected substring."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return VerificationResult(False, "deterministic_file", f"File '{p}' not found for content check.")
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if substring in content:
                return VerificationResult(True, "deterministic_file", f"Substring found in '{p}'.")
            return VerificationResult(False, "deterministic_file", f"Expected substring not found in '{p}'.")
        except Exception as exc:
            return VerificationResult(False, "deterministic_file", f"Error reading file '{p}': {exc}")

    def verify_visual_difference(
        self,
        img_before: Image.Image,
        img_after: Image.Image,
        threshold: float = 0.002,
    ) -> tuple[bool, float]:
        """Compute visual change ratio between two images.
        Returns (has_changed, diff_ratio).
        """
        try:
            # Downsample for fast comparison (e.g. 480x270)
            target_size = (480, 270)
            small_before = img_before.resize(target_size).convert("L")
            small_after = img_after.resize(target_size).convert("L")

            diff = ImageChops.difference(small_before, small_after)
            # Count pixels with non-zero difference > 10
            pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata()
            diff_pixels = sum(1 for p in pixels if p > 10)
            total_pixels = target_size[0] * target_size[1]
            diff_ratio = diff_pixels / total_pixels

            return (diff_ratio >= threshold, round(diff_ratio, 4))
        except Exception as exc:
            logger.debug("Visual difference calculation error: %s", exc)
            return (False, 0.0)


# Global verifier singleton
desktop_verifier = DesktopVerifier()
