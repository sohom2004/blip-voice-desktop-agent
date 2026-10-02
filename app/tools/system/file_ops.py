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

    @staticmethod
    def find_directory(
        query: str,
        search_roots: list[str] | None = None,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        """Search disk for a directory/folder matching a query string without asking the user.
        
        Evaluates common workspace roots (e.g. automations, Documents, Desktop, User profile)
        and scores candidates based on token overlap, slug equality, and last modification recency.
        """
        import os
        import re
        import time

        clean_query = query.strip().lower()
        for noise in ("folder", "directory", "dir", "project", "repo", "in", "the", "my"):
            clean_query = re.sub(rf"\b{noise}\b", "", clean_query).strip()

        if not clean_query:
            clean_query = query.strip().lower()

        slug_query = re.sub(r"[\s_\-]+", "-", clean_query)
        tokens = [t for t in re.split(r"[\s_\-]+", clean_query) if len(t) > 1]

        if not search_roots:
            home = str(Path.home())
            search_roots = [
                r"D:\OneDrive\Desktop\automations",
                r"D:\OneDrive\Desktop",
                str(Path(home) / "source" / "repos"),
                str(Path(home) / "Documents" / "agentic_related_work"),
                str(Path(home) / "Desktop"),
                str(Path(home) / "Documents"),
                str(Path.cwd()),
            ]

        candidates: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        for root_str in search_roots:
            root_path = Path(root_str)
            if not root_path.exists() or not root_path.is_dir():
                continue

            # First check if the root itself matches
            r_name = root_path.name.lower()
            if clean_query in r_name or slug_query in r_name:
                candidates.append({"path": str(root_path), "score": 95, "name": root_path.name})
                seen_paths.add(str(root_path).lower())

            # Walk child directories up to max_depth
            try:
                for current_root, dirs, _ in os.walk(root_path):
                    # Filter out hidden or vendor dirs
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")
                    ]
                    current_path = Path(current_root)
                    depth = len(current_path.relative_to(root_path).parts)
                    if depth > max_depth:
                        dirs.clear()
                        continue

                    for d in dirs:
                        full_dir = current_path / d
                        full_dir_str = str(full_dir)
                        if full_dir_str.lower() in seen_paths:
                            continue
                        seen_paths.add(full_dir_str.lower())

                        d_lower = d.lower()
                        d_slug = re.sub(r"[\s_\-]+", "-", d_lower)

                        score = 0
                        # Exact name match
                        if d_lower == clean_query or d_slug == slug_query:
                            score += 100
                        elif slug_query in d_slug or clean_query in d_lower:
                            score += 80
                        elif tokens and all(t in d_lower or t in d_slug for t in tokens):
                            score += 70
                        elif tokens and any(t in d_lower for t in tokens):
                            score += 40

                        if score > 0:
                            # Recency bonus
                            try:
                                mtime = full_dir.stat().st_mtime
                                age_days = (time.time() - mtime) / 86400
                                if age_days < 7:
                                    score += 15
                                elif age_days < 30:
                                    score += 5
                            except Exception:
                                pass

                            candidates.append({
                                "path": full_dir_str,
                                "name": d,
                                "score": score,
                            })
            except Exception as exc:
                logger.debug("Error walking search root %s: %s", root_str, exc)

        if not candidates:
            return {
                "success": False,
                "error": f"No directory found matching query '{query}'.",
                "query": query,
            }

        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]

        return {
            "success": True,
            "query": query,
            "best_match": best["path"],
            "best_match_name": best["name"],
            "score": best["score"],
            "all_matches": [c["path"] for c in candidates[:5]],
        }


# Global file manager singleton
file_manager = FileManager()
