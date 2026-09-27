"""Master Orchestrator: Bridges Jev System One and Gemini Live Speech Model.

Coordinates fast-path reflexes vs deep reasoning escalation:
1. Direct, high-confidence desktop/window actions are handled by Jev System One in ~150-250ms.
2. Complex, multi-step, coding, or visual tasks are seamlessly handed off
   to the primary Gemini Live speech-to-speech model (gemini-3.1-flash-live-preview)
   equipped with screenshot vision and full desktop automation tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from app.config import settings
from app.jev.router import jev_router

logger = logging.getLogger(__name__)


class MasterOrchestrator:
    """Orchestrates task execution across System One (Jev) and Gemini Live Speech Model."""

    def __init__(self):
        self._activity_callbacks: list[Callable[[dict[str, Any]], None]] = []

    def register_activity_callback(self, callback: Callable[[dict[str, Any]], None]):
        """Subscribe to execution events for real-time UI/CLI feedback."""
        self._activity_callbacks.append(callback)

    def _emit_event(self, event_type: str, data: dict[str, Any]):
        event = {"type": event_type, "timestamp": time.time(), **data}
        for cb in self._activity_callbacks:
            try:
                cb(event)
            except Exception:
                pass

    async def dispatch(self, user_command: str) -> dict[str, Any]:
        """Dispatch user command through the multi-tier agent architecture."""
        start_time = time.perf_counter()
        logger.info("MasterOrchestrator dispatching: '%s'", user_command)

        self._emit_event("task_start", {"command": user_command})

        # Step 1: Query Jev System One Reflex Router
        self._emit_event("system_one_eval_start", {"worker": settings.jev_model})
        state, decision = await jev_router.evaluate_command(user_command)

        self._emit_event(
            "system_one_eval_done",
            {
                "action_type": decision.action_type,
                "needs_escalation": decision.needs_escalation,
                "escalation_prob": decision.escalation_probability,
                "confidence": decision.confidence_level,
                "confidence_score": decision.confidence_score,
            },
        )

        # Step 2: Route according to Jev's judgment
        if not decision.needs_escalation:
            # Fast Reflex Path via Jev System One
            logger.info("Executing via Jev Fast-Path (Action: %s)", decision.action_type)
            self._emit_event("reflex_exec_start", {"action": decision.action_type})

            reflex_res = await jev_router.execute_reflex(user_command)
            elapsed = round(time.perf_counter() - start_time, 3)

            self._emit_event("task_done", {"tier": "system_one_reflex", "duration": elapsed, "result": reflex_res})
            return {
                "tier": "system_one_reflex",
                "action": decision.action_type,
                "message": reflex_res.get("message", "Action completed."),
                "duration_seconds": elapsed,
                "decision": decision.to_dict(),
            }

        # Step 3: Escalation Path to System Two (Complex LLM Worker with Vision & Tools)
        logger.info("Escalating to System Two Complex LLM Worker (reason: complex reasoning / vision needed)")
        self._emit_event("system_two_reasoning_start", {"model": settings.complex_llm_model})

        from app.workers.llm_worker import llm_worker
        loop = asyncio.get_running_loop()
        worker_res = await loop.run_in_executor(
            None,
            lambda: llm_worker.execute_task(user_command, initial_state=state)
        )
        elapsed = round(time.perf_counter() - start_time, 3)

        summary = worker_res.get("summary", "Task executed.")

        self._emit_event("task_done", {"tier": "system_two_reasoning", "duration": elapsed, "result": worker_res})
        return {
            "tier": "system_two_reasoning",
            "action": "complex_task_execution",
            "message": summary,
            "duration_seconds": elapsed,
            "decision": decision.to_dict(),
        }


# Global master orchestrator singleton
orchestrator = MasterOrchestrator()
