"""File Operations Tool.

Inspired by Pi Agent's resilient file management harness:
- Read files with line numbering and slice ranges.
- Safe file creation and overwriting.
- Exact substring patching with boundary verification.
- Directory listing and regex/text search across files.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileManager:
    """Manages file reading, writing, patching, and discovery on the local system."""

    @staticmethod
    def read_file(
        file_path: str | Path,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Read lines from a text file (1-indexed)."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)
            s_idx = max(0, start_line - 1)
            e_idx = end_line if (end_line is not None and end_line <= total_lines) else total_lines

            selected = lines[s_idx:e_idx]
            numbered = [f"{s_idx + i + 1}: {line}" for i, line in enumerate(selected)]

            return {
                "success": True,
                "file_path": str(p),
                "total_lines": total_lines,
                "start_line": s_idx + 1,
                "end_line": e_idx,
                "content": "".join(numbered),
                "raw_content": "".join(selected),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def write_file(
        file_path: str | Path,
        content: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Create or overwrite a file with the given content."""
        p = Path(file_path).resolve()
        if p.exists() and not overwrite:
            return {"success": False, "error": f"File already exists: {file_path}"}

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "success": True,
                "file_path": str(p),
                "bytes_written": len(content.encode("utf-8")),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def patch_file(
        file_path: str | Path,
        target_content: str,
        replacement_content: str,
    ) -> dict[str, Any]:
        """Replace exact target_content substring in file with replacement_content."""
        p = Path(file_path).resolve()
        if not p.is_file():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()

            if target_content not in content:
                return {
                    "success": False,
                    "error": "Target content not found in file for patch.",
                }

            count = content.count(target_content)
            if count > 1:
                return {
                    "success": False,
                    "error": f"Target content found multiple times ({count}). Target must be unique.",
                }

            new_content = content.replace(target_content, replacement_content, 1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(new_content)

            return {"success": True, "file_path": str(p), "replaced": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def list_dir(
        dir_path: str | Path = ".",
        pattern: str = "*",
        recursive: bool = False,
    ) -> dict[str, Any]:
        """List files and folders in a directory matching an optional glob pattern."""
        p = Path(dir_path).resolve()
        if not p.is_dir():
            return {"success": False, "error": f"Directory not found: {dir_path}"}

        try:
            items = []
            iterator = p.rglob(pattern) if recursive else p.glob(pattern)
            for item in iterator:
                items.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.is_file() else None,
                    }
                )
            return {"success": True, "dir": str(p), "count": len(items), "items": items}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def search_in_files(
        query: str,
        dir_path: str | Path = ".",
        file_pattern: str = "*.py",
        max_matches: int = 50,
    ) -> dict[str, Any]:
        """Search for a text query inside files matching the file_pattern."""
        p = Path(dir_path).resolve()
        if not p.is_dir():
            return {"success": False, "error": f"Directory not found: {dir_path}"}

        matches = []
        try:
            for file_path in p.rglob(file_pattern):
                if not file_path.is_file():
                    continue
                # Skip .git and venv
                if any(part in file_path.parts for part in (".git", "__pycache__", ".venv", "node_modules")):
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, start=1):
                            if query.lower() in line.lower():
                                matches.append(
                                    {
                                        "file": str(file_path),
                                        "line_number": line_num,
                                        "line": line.strip(),
                                    }
                                )
                                if len(matches) >= max_matches:
                                    break
                except Exception:
                    continue

                if len(matches) >= max_matches:
                    break

            return {"success": True, "query": query, "count": len(matches), "matches": matches}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# Global file manager singleton
file_manager = FileManager()
