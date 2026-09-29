"""Hybrid Desktop Execution Package.

Provides capability resolution, deterministic verification, security guardrails,
state-aware recovery ladders, and multi-tier desktop automation.
"""

from app.execution.cua_backend import CuaBackend, cua_backend
from app.execution.executor import DesktopExecutor, desktop_executor
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
from app.execution.policy import RiskTier, SecurityPolicyGuard, policy_guard
from app.execution.recovery import RecoveryManager, recovery_manager
from app.execution.resolver import CapabilityResolver, ExecutionPlan, capability_resolver
from app.execution.verifier import DesktopVerifier, desktop_verifier

__all__ = [
    "DesktopAction",
    "DesktopTarget",
    "ExpectedState",
    "Observation",
    "VerificationResult",
    "ExecutionAttempt",
    "ExecutionResult",
    "ExecutionBackend",
    "RiskTier",
    "SecurityPolicyGuard",
    "policy_guard",
    "CapabilityResolver",
    "ExecutionPlan",
    "capability_resolver",
    "DesktopVerifier",
    "desktop_verifier",
    "RecoveryManager",
    "recovery_manager",
    "CuaBackend",
    "cua_backend",
    "DesktopExecutor",
    "desktop_executor",
]
