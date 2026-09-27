"""FastAPI Server for Voice Desktop Application.

Provides REST endpoints and WebSocket event streaming for:
- Desktop state and window management
- Command dispatching via Master Orchestrator (Jev System One + LLM Worker)
- LiveKit voice token provisioning
- Real-time activity telemetry
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.tools.desktop.window_manager import window_manager
from app.voice.token_service import generate_livekit_token
from app.workers.orchestrator import orchestrator

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Voice Desktop API",
    description="Dual-tier agentic desktop automation powered by TypeSafe Jev and Gemini",
    version="1.0.0",
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active WebSocket connections for telemetry
_connected_websockets: list[WebSocket] = []


def _broadcast_event_sync(event: dict[str, Any]):
    """Push orchestrator event to connected websockets."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast_event(event))
    except RuntimeError:
        pass


async def _broadcast_event(event: dict[str, Any]):
    disconnected = []
    for ws in _connected_websockets:
        try:
            await ws.send_json(event)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in _connected_websockets:
            _connected_websockets.remove(ws)


# Register event listener with orchestrator
orchestrator.register_activity_callback(_broadcast_event_sync)


# ---------------------------------------------------------------------------
# Request/Response Schemas
# ---------------------------------------------------------------------------

class CommandRequest(BaseModel):
    command: str


class FocusWindowRequest(BaseModel):
    query: str


class TokenRequest(BaseModel):
    room_name: str | None = None
    participant_name: str = "Desktop User"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health status and configuration readiness."""
    return {
        "status": "online",
        "app": "voice-desktop",
        "jev_configured": settings.is_jev_configured(),
        "gemini_configured": settings.is_gemini_configured(),
        "livekit_configured": settings.is_livekit_configured(),
    }


@app.get("/api/status")
async def get_system_status():
    """Current desktop state summary."""
    active = window_manager.get_active_window()
    windows = window_manager.list_windows()
    return {
        "active_window": active.to_dict() if active else None,
        "total_windows": len(windows),
        "jev_model": settings.jev_model,
        "gemini_live_model": settings.gemini_live_model,
    }


@app.get("/api/windows")
async def list_windows():
    """List all open application windows."""
    windows = window_manager.list_windows()
    return {"count": len(windows), "windows": [w.to_dict() for w in windows]}


@app.post("/api/windows/focus")
async def focus_window_endpoint(req: FocusWindowRequest):
    """Bring a window to the front."""
    success = window_manager.bring_to_front(req.query)
    if not success:
        raise HTTPException(status_code=404, detail=f"Window '{req.query}' not found or could not be focused.")
    return {"success": True, "focused": req.query}


@app.post("/api/command")
async def dispatch_command(req: CommandRequest):
    """Dispatch a user command through Master Orchestrator."""
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty.")
    result = await orchestrator.dispatch(req.command)
    return result


@app.post("/api/livekit/token")
@app.post("/api/token")
async def get_voice_token(req: dict[str, Any] | None = None):
    """Provision a LiveKit access token for voice sessions."""
    req_dict = req or {}
    room = req_dict.get("roomName") or req_dict.get("room_name")
    identity = req_dict.get("identity")
    participant_name = req_dict.get("participant_name") or "Desktop User"
    try:
        token_info = generate_livekit_token(
            room_name=room,
            identity=identity,
            participant_name=participant_name,
        )
        return {
            "token": token_info["token"],
            "roomName": token_info["room_name"],
            "room_name": token_info["room_name"],
            "identity": token_info["identity"],
            "url": token_info["url"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/documents")
async def list_documents(workspaceId: str = "default_workspace"):
    """Document list endpoint for web UI compatibility."""
    return []


@app.post("/api/documents/upload")
async def upload_document():
    return {"status": "ok", "message": "Documents ready"}


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    return {"status": "ok"}


@app.post("/api/documents/reset-samples")
async def reset_sample_documents():
    return {"status": "ok", "documents": []}


@app.post("/api/tts")
async def test_tts(payload: dict[str, Any] | None = None):
    return {"status": "ok"}


@app.get("/api/sql-mcp/connection-status")
@app.get("/api/sql-mcp/active-connection")
async def sql_mcp_status():
    return {
        "connected": True,
        "dialect": "Windows Desktop",
        "database": "Voice Desktop OS",
        "tables": ["Open Windows", "Accessibility Controls", "PowerShell", "Browser DOM"],
    }


@app.get("/api/sql-mcp/logs")
async def sql_mcp_logs():
    return {
        "logs": [
            "Voice Desktop OS Ready",
            f"System One Reflex: {settings.jev_model}",
            f"LiveKit Gemini Live: {settings.gemini_live_model}",
        ]
    }


@app.get("/api/health")
async def api_health():
    return {
        "status": "ok",
        "backend": "fastapi",
        "app": "voice-desktop",
        "voice": "livekit",
    }


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket stream for real-time execution telemetry."""
    await websocket.accept()
    _connected_websockets.append(websocket)
    try:
        while True:
            # Keepalive ping
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _connected_websockets:
            _connected_websockets.remove(websocket)


# Mount Web UI frontend static files
from fastapi.staticfiles import StaticFiles
from app.config import ROOT_DIR

_DIST = ROOT_DIR / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.server_host, port=settings.server_port, reload=True)
