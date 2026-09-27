"""LiveKit Access Token Service.

Generates JWT access tokens for connecting to LiveKit rooms.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from livekit import api
from app.config import settings


def generate_livekit_token(
    room_name: str | None = None,
    identity: str | None = None,
    participant_name: str = "Desktop User",
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Generate a JWT token for connecting to the LiveKit voice room."""
    if not settings.is_livekit_configured():
        raise RuntimeError("LiveKit credentials (URL, API Key, Secret) are not fully configured.")

    room = room_name or f"desktop-voice-{uuid.uuid4().hex[:6]}"
    user_identity = identity or f"user-{uuid.uuid4().hex[:6]}"
    meta = metadata or {"role": "user", "app": "voice-desktop"}

    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(user_identity)
        .with_name(participant_name)
        .with_metadata(json.dumps(meta))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    return {
        "token": token,
        "room_name": room,
        "identity": user_identity,
        "url": settings.livekit_url,
    }
