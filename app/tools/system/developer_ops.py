"""Developer Operations & System Control Tools.

Inspired by OpenCode and Pi Agent harnesses:
- Visual Directory Tree (`get_directory_tree`)
- Fast Code & Regex Search (`grep_code`)
- Git Repository Status & History (`get_git_status`)
- Port & Process Inspector / Terminator (`find_process_by_port`, `kill_process`)
- Lightweight Python Script Runner (`execute_python_code`)
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)


class DeveloperOps:
    """Developer operations and system control tools."""

    @staticmethod
    def get_directory_tree(
        dir_path: str | Path = ".",
        max_depth: int = 2,
        max_entries: int = 50,
    ) -> str:
        """Render a clean ASCII visual directory hierarchy up to max_depth."""
        root = Path(dir_path).resolve()
        if not root.is_dir():
            return f"Error: '{dir_path}' is not a valid directory."

        lines = [f"{root.name}/"]
        count = 0

        ignored = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "dist", "build", ".pytest_cache", ".gemini", ".cursor"
        }

        def _walk(current: Path, prefix: str, depth: int):
            nonlocal count
            if depth > max_depth or count >= max_entries:
                return

            try:
                children = sorted(
                    [c for c in current.iterdir() if c.name not in ignored and not c.name.startswith(".")],
                    key=lambda x: (not x.is_dir(), x.name.lower()),
                )
            except Exception:
                return

            total = len(children)
            for idx, child in enumerate(children):
                if count >= max_entries:
                    lines.append(f"{prefix}... (truncated after {max_entries} entries)")
                    return

                count += 1
                is_last = (idx == total - 1)
                connector = "\\-- " if is_last else "|-- "
                sub_prefix = "    " if is_last else "|   "

                if child.is_dir():
                    lines.append(f"{prefix}{connector}{child.name}/")
                    _walk(child, prefix + sub_prefix, depth + 1)
                else:
                    lines.append(f"{prefix}{connector}{child.name}")

        _walk(root, "", 1)
        return "\n".join(lines)

    @staticmethod
    def grep_code(
        query: str,
        dir_path: str | Path = ".",
        file_pattern: str = "*.*",
        max_matches: int = 35,
    ) -> str:
        """Search across files using regex or plain text with line numbers and matched lines."""
        root = Path(dir_path).resolve()
        if not root.is_dir():
            return f"Directory not found: {dir_path}"

        matches: list[str] = []
        ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)

        for current_root, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]

            for fname in files:
                if fname.startswith("."):
                    continue
                if file_pattern != "*.*" and not fname.endswith(file_pattern.replace("*", "")):
                    continue

                full_path = Path(current_root) / fname
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for l_num, line in enumerate(f, start=1):
                            if pattern.search(line):
                                rel_path = full_path.relative_to(root)
                                matches.append(f"{rel_path}:{l_num}: {line.strip()[:120]}")
                                if len(matches) >= max_matches:
                                    break
                except Exception:
                    continue

                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

        if not matches:
            return f"No matches found for query '{query}' in {root.name}."
        return f"Found {len(matches)} match(es) for '{query}':\n" + "\n".join(matches)

    @staticmethod
    def get_git_status(repo_path: str | Path = ".") -> str:
        """Inspect Git repository status: branch, modified files, and recent commit."""
        root = Path(repo_path).resolve()
        git_dir = root / ".git"
        if not git_dir.exists():
            return f"No Git repository found at '{root}'."

        try:
            branch = subprocess.check_output(
                ["git", "branch", "--show-current"],
                cwd=str(root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()

            status = subprocess.check_output(
                ["git", "status", "--short"],
                cwd=str(root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()

            log = subprocess.check_output(
                ["git", "log", "-1", "--oneline"],
                cwd=str(root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()

            changed_lines = status.split("\n") if status else []
            return (
                f"Git Repo: {root.name}\n"
                f"Branch: {branch or 'detached'}\n"
                f"Latest Commit: {log}\n"
                f"Changed Files ({len(changed_lines)}):\n"
                + (status if status else "Working tree clean.")
            )
        except Exception as exc:
            return f"Error reading git status: {exc}"

    @staticmethod
    def find_process_by_port(port: int) -> dict[str, Any]:
        """Find the process listening on a specific network port (e.g. 8000, 3000, 5173)."""
        listeners = []
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                    pid = conn.pid
                    proc_name = "unknown"
                    if pid:
                        try:
                            proc_name = psutil.Process(pid).name()
                        except Exception:
                            pass
                    listeners.append({"pid": pid, "process_name": proc_name, "port": port})
            if listeners:
                return {"success": True, "port": port, "processes": listeners}
            return {"success": False, "port": port, "message": f"No process listening on port {port}."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def kill_process(name_or_pid: str | int) -> dict[str, Any]:
        """Terminate a running process by name (e.g. 'node.exe', 'python.exe') or PID."""
        terminated = []
        try:
            if isinstance(name_or_pid, int) or str(name_or_pid).isdigit():
                pid = int(name_or_pid)
                p = psutil.Process(pid)
                p_name = p.name()
                p.kill()
                return {"success": True, "killed": [{"pid": pid, "name": p_name}]}

            target_name = str(name_or_pid).lower().replace(".exe", "")
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    p_base = (p.info["name"] or "").lower().replace(".exe", "")
                    if p_base == target_name:
                        p.kill()
                        terminated.append({"pid": p.info["pid"], "name": p.info["name"]})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if terminated:
                return {"success": True, "killed": terminated, "count": len(terminated)}
            return {"success": False, "error": f"No running process matching '{name_or_pid}' found."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def execute_python_code(code: str, timeout: float = 10.0) -> str:
        """Execute a Python snippet in an isolated subprocess and return stdout/stderr."""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = proc.stdout.strip()
            err = proc.stderr.strip()
            if proc.returncode == 0:
                return out or "Executed successfully (no output)."
            return f"Python error (exit {proc.returncode}):\n{err or out}"
        except subprocess.TimeoutExpired:
            return f"Execution timed out after {timeout} seconds."
        except Exception as exc:
            return f"Execution error: {exc}"


# Global developer operations singleton
dev_ops = DeveloperOps()
