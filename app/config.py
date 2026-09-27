"""Configuration loader for the Voice Desktop application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent.parent

# Load .env file
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH, override=True)


@dataclass(frozen=True)
class Settings:
    # Root Paths
    root_dir: Path = ROOT_DIR

    # TypeSafe / Jev (System One Reflex Worker)
    typesafe_api_key: str = os.getenv("TYPESAFE_API_KEY", "")
    jev_base_url: str = os.getenv("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone")
    jev_model: str = os.getenv("JEV_MODEL", "jev-latest")

    # Google Gemini & Realtime
    # NOTE: The speech-to-speech model is strictly gemini-3.1-flash-live-preview (do not change under any condition)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    gemini_live_model: str = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
    gemini_live_voice: str = os.getenv("GEMINI_LIVE_VOICE", "Kore")
    complex_llm_model: str = os.getenv("COMPLEX_LLM_MODEL", "gemini-3.6-flash")

    # LiveKit
    livekit_url: str = os.getenv("LIVEKIT_URL", "")
    livekit_api_key: str = os.getenv("LIVEKIT_API_KEY", "")
    livekit_api_secret: str = os.getenv("LIVEKIT_API_SECRET", "")

    # Server & Runtime
    server_host: str = os.getenv("SERVER_HOST", "127.0.0.1")
    server_port: int = int(os.getenv("SERVER_PORT", "8000"))
    app_env: str = os.getenv("APP_ENV", "development")

    def __post_init__(self):
        # Auto-mirror GEMINI_API_KEY to GOOGLE_API_KEY if needed by LiveKit plugins
        if self.gemini_api_key and not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = self.gemini_api_key
        if self.typesafe_api_key and not os.environ.get("TYPESAFE_API_KEY"):
            os.environ["TYPESAFE_API_KEY"] = self.typesafe_api_key

    def is_jev_configured(self) -> bool:
        return bool(self.typesafe_api_key and self.typesafe_api_key.startswith("apikey_"))

    def is_gemini_configured(self) -> bool:
        return bool(self.gemini_api_key or self.google_api_key)

    def is_livekit_configured(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)


# Global singleton instance
settings = Settings()
