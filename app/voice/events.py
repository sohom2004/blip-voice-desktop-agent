"""Voice and Desktop Event Bridge.

Manages data-channel events for LiveKit rooms and broadcasts real-time
agent status, transcription, and tool execution phases.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Active room reference
_active_room: Any | None = None


def set_active_room(room: Any | None):
    global _active_room
    _active_room = room


def get_active_room() -> Any | None:
    return _active_room


async def publish_event(payload: dict[str, Any], room: Any | None = None) -> bool:
    """Publish a structured event over the LiveKit data channel."""
    target_room = room or _active_room
    if target_room is None:
        logger.debug("publish_event dropped: no active room for payload type=%s", payload.get("type"))
        return False

    try:
        local_participant = getattr(target_room, "local_participant", None)
        if local_participant is None:
            logger.debug("publish_event dropped: local_participant is None")
            return False

        data = json.dumps(payload, default=str).encode("utf-8")
        await local_participant.publish_data(data, reliable=True)
        return True
    except Exception as exc:
        logger.warning("Failed to publish data-channel event (%s): %s", payload.get("type"), exc)
        return False

