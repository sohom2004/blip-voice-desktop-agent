"""Execution Layer Data Models for Hybrid Desktop Automation.

Defines actions, targets, expected states, observations, verifications,
and execution traces across all automation tiers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ExecutionBackend(str, Enum):
    """Hierarchical execution backends in order of deterministic preference."""
    NATIVE_API = "native_api"        # 1. Direct App API / CLI
    OS_SYSTEM = "os_system"          # 2. OS APIs (filesystem, powershell, process)
    BROWSER_DOM = "browser_dom"      # 3. Playwright / CDP DOM interaction
    UI_AUTOMATION = "ui_automation"  # 4. Windows UI Automation (Value, Invoke, Toggle, etc.)
    WIN32 = "win32"                  # 5. Direct Win32 window messaging (WM_CLOSE, SetForeground)
    KEYBOARD = "keyboard"            # 6. Global keyboard shortcuts / hotkeys
    CUA = "cua"                      # 7. Computer-use background driver
    PYAUTOGUI = "pyautogui"          # 8. Asserted mouse cursor / click
    VISION = "vision"                # 9. MSS Framebuffer / crop vision check
    COORDINATES = "coordinates"      # 10. Last resort blind screen coordinates


@dataclass
class DesktopTarget:
    """Target entity for desktop execution (window, element, file, process, coordinates)."""
    window_query: str | int | None = None
    hwnd: int | None = None
    pid: int | None = None
    process_name: str | None = None
    element_id: str | None = None
    element_name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None
    class_name: str | None = None
    dom_selector: str | None = None
    file_path: str | None = None
    coordinates: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExpectedState:
    """Deterministic postcondition assertion criteria."""
    window_state: str | None = None             # "active", "visible", "closed", "minimized"
    window_title_contains: str | None = None
    process_running: str | None = None          # e.g. "notepad.exe"
    process_terminated: str | None = None
    file_exists: str | None = None
    file_not_exists: str | None = None
    file_content_contains: tuple[str, str] | None = None  # (path, substring)
    element_value_equals: str | None = None
    element_exists: bool | None = None
    visual_change_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DesktopAction:
    """Structured desktop action to be dispatched and verified."""
    action_type: str
    target: DesktopTarget = field(default_factory=DesktopTarget)
    params: dict[str, Any] = field(default_factory=dict)
    expected_state: ExpectedState | None = None
    preferred_backend: ExecutionBackend | None = None
    allow_fallback: bool = True
    timeout: float = 10.0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target.to_dict(),
            "params": self.params,
            "expected_state": self.expected_state.to_dict() if self.expected_state else None,
            "preferred_backend": self.preferred_backend.value if self.preferred_backend else None,
            "allow_fallback": self.allow_fallback,
            "timeout": self.timeout,
            "description": self.description,
        }


@dataclass
class Observation:
    """Post-action desktop state capture."""
    active_window: dict[str, Any] | None = None
    open_windows_count: int = 0
    element_value: str | None = None
    exit_code: int | None = None
    command_output: str = ""
    file_status: dict[str, Any] | None = None
    screenshot_path: str | None = None
    visual_diff_ratio: float | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    """Result of state verification assertion."""
    verified: bool
    check_type: str  # "deterministic_window", "deterministic_file", "deterministic_process", "deterministic_uia", "visual", "none"
    message: str
    confidence: float = 1.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionAttempt:
    """Audit entry for a single backend execution attempt."""
    backend: ExecutionBackend
    timestamp: float
    success: bool
    message: str
    verification: VerificationResult | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend.value,
            "timestamp": self.timestamp,
            "success": self.success,
            "message": self.message,
            "verification": self.verification.to_dict() if self.verification else None,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class ExecutionResult:
    """Comprehensive result of hybrid execution with full audit trail."""
    success: bool
    action_type: str
    backend_used: ExecutionBackend | None = None
    message: str = ""
    attempts: list[ExecutionAttempt] = field(default_factory=list)
    final_observation: Observation | None = None
    duration_seconds: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action_type": self.action_type,
            "backend_used": self.backend_used.value if self.backend_used else None,
            "message": self.message,
            "attempts": [a.to_dict() for a in self.attempts],
            "final_observation": self.final_observation.to_dict() if self.final_observation else None,
            "duration_seconds": self.duration_seconds,
            "trace": self.trace,
        }
