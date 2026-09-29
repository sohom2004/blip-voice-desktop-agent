"""Security Policy Guard for Desktop Automation.

Classifies operations by risk tier:
- READ_ONLY: Safe inspection, queries, listings, captures.
- LOW_RISK: User app interaction (focus, click, type, scroll, user file write).
- HIGH_RISK: Process termination, file deletion, system modification.
- BLOCKED: Critical Windows OS processes or path deletion.
"""

from __future__ import annotations

import logging
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

from app.execution.models import DesktopAction

logger = logging.getLogger(__name__)


class RiskTier(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    HIGH_RISK = "high_risk"
    BLOCKED = "blocked"


CRITICAL_SYSTEM_PROCESSES = {
    "csrss.exe",
    "lsass.exe",
    "smss.exe",
    "services.exe",
    "wininit.exe",
    "winlogon.exe",
    "system",
    "idle",
    "registry",
    "ntoskrnl.exe",
}

BLOCKED_COMMAND_PATTERNS = [
    r"\bformat\s+[a-zA-Z]:",
    r"\bdel(ete)?(\s+/[a-zA-Z]+)*\s+[a-zA-Z]:\\?",
    r"\brmdir(\s+/[a-zA-Z]+)*\s+[a-zA-Z]:\\?",
    r"\bRemove-Item\s+-Recurse\s+-[Ff]orce\s+[a-zA-Z]:\\?$",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
]

PROTECTED_PATHS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve(),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).resolve(),
]


class SecurityPolicyGuard:
    """Evaluates desktop actions against security boundaries and safety rules."""

    def __init__(self, allow_high_risk: bool = True):
        self.allow_high_risk = allow_high_risk

    def evaluate_action(self, action: DesktopAction) -> tuple[RiskTier, str]:
        """Classify the action and provide reasoning."""
        atype = action.action_type.lower()

        # 1. READ ONLY actions
        if atype in {
            "inspect",
            "inspect_screen",
            "observe",
            "list_windows",
            "read_file",
            "list_dir",
            "search_files",
            "screen_capture",
            "get_value",
            "check_status",
        }:
            return RiskTier.READ_ONLY, "Read-only inspection operation."

        # 2. Process management checks
        if atype in {"kill_process", "terminate_process"}:
            proc_target = (
                str(action.target.process_name or action.params.get("process", "")).strip().lower()
            )
            if not proc_target.endswith(".exe") and proc_target:
                proc_target += ".exe"

            if proc_target in CRITICAL_SYSTEM_PROCESSES:
                return (
                    RiskTier.BLOCKED,
                    f"Blocked attempt to kill critical system process: '{proc_target}'.",
                )
            return RiskTier.HIGH_RISK, f"Terminating process '{proc_target}' is high-risk."

        # 3. Terminal command checks
        if atype in {"terminal_command", "execute_terminal", "run_terminal"}:
            cmd = str(action.params.get("command", "")).strip()
            for pattern in BLOCKED_COMMAND_PATTERNS:
                if re.search(pattern, cmd, re.IGNORECASE):
                    return (
                        RiskTier.BLOCKED,
                        f"Blocked dangerous command matching destructive pattern '{pattern}'.",
                    )
            # Check for deletion / kill in command string
            if any(kw in cmd.lower() for kw in ("taskkill", "stop-process", "remove-item", "rmdir", "del ")):
                return RiskTier.HIGH_RISK, "Terminal command includes deletion or process termination."
            return RiskTier.LOW_RISK, "Standard terminal command."

        # 4. File operations checks
        if atype in {"delete_file", "remove_file", "delete_dir"}:
            file_target = action.target.file_path or action.params.get("file_path")
            if file_target:
                p = Path(file_target).resolve()
                for prot in PROTECTED_PATHS:
                    if p == prot or prot in p.parents:
                        return (
                            RiskTier.BLOCKED,
                            f"Blocked attempt to delete system protected path: '{p}'.",
                        )
                return RiskTier.HIGH_RISK, f"File deletion request for '{p}'."
            return RiskTier.HIGH_RISK, "File deletion requested."

        if atype in {"write_file", "patch_file"}:
            file_target = action.target.file_path or action.params.get("file_path")
            if file_target:
                p = Path(file_target).resolve()
                for prot in PROTECTED_PATHS:
                    if p == prot or prot in p.parents:
                        return (
                            RiskTier.BLOCKED,
                            f"Blocked attempt to write inside system directory: '{p}'.",
                        )
            return RiskTier.LOW_RISK, "User file creation/modification."

        # 5. Standard desktop GUI actions
        if atype in {
            "launch_app",
            "focus_window",
            "minimize_window",
            "maximize_window",
            "close_window",
            "click_element",
            "set_value",
            "type_text",
            "keyboard_shortcut",
            "hotkey",
            "scroll",
            "open_web",
            "browser_navigate",
            "browser_click",
            "browser_type",
        }:
            return RiskTier.LOW_RISK, "Safe user interface automation."

        return RiskTier.LOW_RISK, "Default low-risk desktop action."

    def is_allowed(self, action: DesktopAction) -> tuple[bool, str]:
        """Check if action is permitted to run under current security policy."""
        tier, reason = self.evaluate_action(action)
        if tier == RiskTier.BLOCKED:
            logger.error("Security policy BLOCKED action: %s (%s)", action.action_type, reason)
            return False, reason
        if tier == RiskTier.HIGH_RISK and not self.allow_high_risk:
            logger.warning("Security policy rejected HIGH_RISK action: %s (%s)", action.action_type, reason)
            return False, reason
        return True, reason


# Global security guard singleton
policy_guard = SecurityPolicyGuard(allow_high_risk=True)
