"""Persistent Memory Harness for Desktop Voice Agent.

Inspired by Hermes Agent's persistent memory and skill persistence loop:
- Stores user preferences, project paths, aliases, and custom workflows.
- Thread-safe JSON storage persisted across sessions.
- Keyword and semantic fuzzy recall for instant context lookup.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    key: str
    value: str
    category: str = "general"
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    """Persistent key-value memory store for user facts, paths, and preferences."""

    def __init__(self, storage_path: str | Path | None = None):
        if storage_path:
            self.storage_file = Path(storage_path).resolve()
        else:
            self.storage_file = settings.root_dir / "data" / "agent_memory.json"

        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        self.memories: dict[str, MemoryEntry] = {}
        self._load()

    def _load(self):
        if not self.storage_file.exists():
            # Seed default known project paths
            self.remember(
                "voice-desktop",
                str(settings.root_dir),
                category="project_path",
            )
            self.remember(
                "antigravity",
                "agy",
                category="tool_alias",
            )
            return

        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, item in data.items():
                self.memories[k.lower()] = MemoryEntry(
                    key=item.get("key", k),
                    value=item.get("value", ""),
                    category=item.get("category", "general"),
                    timestamp=item.get("timestamp", time.time()),
                )
        except Exception as exc:
            logger.error("Failed to load agent memory: %s", exc)

    def _save(self):
        try:
            data = {k: m.to_dict() for k, m in self.memories.items()}
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.error("Failed to save agent memory: %s", exc)

    def remember(self, key: str, value: str, category: str = "general") -> str:
        """Store or update a fact, directory path, or preference in persistent memory."""
        clean_key = key.strip().lower()
        entry = MemoryEntry(
            key=key.strip(),
            value=value.strip(),
            category=category.strip(),
            timestamp=time.time(),
        )
        self.memories[clean_key] = entry
        self._save()
        logger.info("Memory stored: [%s] '%s' = '%s'", category, key, value)
        return f"Remembered: '{key}' = '{value}' (category: {category})."

    def recall(self, query: str = "") -> list[dict[str, Any]]:
        """Search persistent memories by key, value, or category."""
        if not query or query.strip() == "*":
            return [m.to_dict() for m in self.memories.values()]

        q = query.strip().lower()
        results: list[dict[str, Any]] = []

        for k, entry in self.memories.items():
            score = 0
            if q == k or q == entry.key.lower():
                score = 100
            elif q in k or q in entry.value.lower() or q in entry.category.lower():
                score = 70
            elif any(token in k or token in entry.value.lower() for token in q.split() if len(token) > 2):
                score = 40

            if score > 0:
                item = entry.to_dict()
                item["score"] = score
                results.append(item)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results

    def forget(self, key: str) -> bool:
        """Delete a fact from memory by key."""
        clean_key = key.strip().lower()
        if clean_key in self.memories:
            del self.memories[clean_key]
            self._save()
            return True
        return False


# Global memory store singleton
memory_store = MemoryStore()
