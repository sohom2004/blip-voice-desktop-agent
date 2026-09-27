"""TypeSafe Jev System One Client.

Manages HTTP communication with the TypeSafe System One evaluation endpoint.
Implements exponential backoff on 429 and 529 status codes per documentation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class JevClient:
    """Async client for TypeSafe System One (Jev)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 15.0,
    ):
        self.api_key = api_key or settings.typesafe_api_key
        self.base_url = base_url or settings.jev_base_url
        self.model = model or settings.jev_model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def evaluate(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, dict[str, Any]],
        model: str | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Send state and typed questions to TypeSafe System One for evaluation."""
        if not self.api_key:
            raise ValueError("TypeSafe API key is not configured in settings or environment.")

        client = await self._get_client()
        payload = {
            "state": state,
            "model": model or self.model,
            "questions": questions,
        }

        delay = 1.0
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = await client.post(self.base_url, json=payload)

                if response.status_code == 200:
                    return response.json()

                if response.status_code in (429, 529):
                    logger.warning(
                        "TypeSafe API rate limit or overload (%d). Retrying in %.1fs (attempt %d/%d)...",
                        response.status_code,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2.0
                    continue

                # 401, 422, or other errors
                response.raise_for_status()

            except httpx.HTTPStatusError as exc:
                last_error = exc
                body = exc.response.text
                logger.error("TypeSafe HTTP error %d: %s", exc.response.status_code, body)
                raise RuntimeError(f"TypeSafe API failed ({exc.response.status_code}): {body}") from exc
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning("TypeSafe connection error (%s). Retrying in %.1fs...", exc, delay)
                await asyncio.sleep(delay)
                delay *= 2.0

        raise RuntimeError(f"TypeSafe evaluation exceeded max retries: {last_error}")

    async def close(self):
        """Close internal HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Global Jev client singleton
jev_client = JevClient()
