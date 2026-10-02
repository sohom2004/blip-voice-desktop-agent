"""Asynchronous Terminal and Process Execution Tool.

Inspired by Hermes Agent and Pi Agent command harnesses:
- Supports PowerShell / CMD command execution with timeouts.
- Captures stdout/stderr, return codes, and execution duration.
- Manages background tasks (start, poll, kill).
- Tracks and persists working directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def output(self) -> str:
        out = self.stdout
        if self.stderr:
            out += ("\n" if out else "") + f"[stderr]\n{self.stderr}"
        return out.strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackgroundJob:
    job_id: str
    command: str
    process: asyncio.subprocess.Process
    start_time: float
    cwd: str
    output_buffer: list[str]
    is_finished: bool = False
    exit_code: int | None = None


class TerminalManager:
    """Manages asynchronous command and process execution in the desktop environment."""

    def __init__(self, default_cwd: str | Path | None = None):
        self.cwd = str(Path(default_cwd or os.getcwd()).resolve())
        self.background_jobs: dict[str, BackgroundJob] = {}

    def get_cwd(self) -> str:
        return self.cwd

    def set_cwd(self, path: str | Path) -> bool:
        resolved = Path(path).resolve()
        if resolved.is_dir():
            self.cwd = str(resolved)
            return True
        return False

    async def execute(
        self,
        command: str,
        timeout: float = 30.0,
        shell: str = "powershell.exe",
    ) -> CommandResult:
        """Run a command synchronously to completion with timeout."""
        start_time = time.perf_counter()
        logger.info("Executing terminal command: %s (cwd: %s)", command, self.cwd)

        # Build shell invocation
        if "powershell" in shell.lower():
            cmd_args = [shell, "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            cmd_args = ["cmd.exe", "/c", command]

        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                cwd=self.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                exit_code = proc.returncode
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                stdout_data, stderr_data = b"", b"Command timed out after " + str(timeout).encode() + b"s"
                exit_code = -1

            duration = round(time.perf_counter() - start_time, 3)
            stdout = stdout_data.decode("utf-8", errors="replace")
            stderr = stderr_data.decode("utf-8", errors="replace")

            return CommandResult(
                command=command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                timed_out=timed_out,
            )

        except Exception as exc:
            duration = round(time.perf_counter() - start_time, 3)
            logger.error("Terminal execution failed: %s", exc)
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
                timed_out=False,
            )

    async def start_background(self, command: str, shell: str = "powershell.exe") -> str:
        """Start a long-running process in the background and return its job ID."""
        job_id = f"job_{uuid.uuid4().hex[:8]}"

        if "powershell" in shell.lower():
            cmd_args = [shell, "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            cmd_args = ["cmd.exe", "/c", command]

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        job = BackgroundJob(
            job_id=job_id,
            command=command,
            process=proc,
            start_time=time.time(),
            cwd=self.cwd,
            output_buffer=[],
        )
        self.background_jobs[job_id] = job

        # Stream reader task
        async def _read_stream(stream: asyncio.StreamReader | None):
            if not stream:
                return
            while not stream.at_eof():
                line = await stream.readline()
                if line:
                    decoded = line.decode("utf-8", errors="replace")
                    job.output_buffer.append(decoded)

        async def _monitor():
            await asyncio.gather(
                _read_stream(proc.stdout),
                _read_stream(proc.stderr),
            )
            job.exit_code = await proc.wait()
            job.is_finished = True

        asyncio.create_task(_monitor())
        return job_id

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the current status and buffered output of a background job."""
        job = self.background_jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "command": job.command,
            "is_finished": job.is_finished,
            "exit_code": job.exit_code,
            "running_seconds": round(time.time() - job.start_time, 2),
            "output": "".join(job.output_buffer[-50:]),  # last 50 lines
        }

    def kill_job(self, job_id: str) -> bool:
        """Terminate a background job."""
        job = self.background_jobs.get(job_id)
        if not job or job.is_finished:
            return False
        try:
            job.process.kill()
            return True
        except Exception:
            return False

    async def await_job(self, job_id: str, timeout: float = 30.0) -> CommandResult:
        """Wait for a background job to finish and return its full output and exit code."""
        job = self.background_jobs.get(job_id)
        if not job:
            return CommandResult(
                command="unknown",
                exit_code=-1,
                stdout="",
                stderr=f"Background job '{job_id}' not found.",
                duration_seconds=0.0,
            )

        start = time.perf_counter()
        while not job.is_finished:
            if time.perf_counter() - start > timeout:
                return CommandResult(
                    command=job.command,
                    exit_code=None,
                    stdout="".join(job.output_buffer[-40:]),
                    stderr=f"Timed out waiting for job {job_id} after {timeout:.1f}s (process still running).",
                    duration_seconds=round(time.perf_counter() - start, 2),
                    timed_out=True,
                )
            await asyncio.sleep(0.2)

        return CommandResult(
            command=job.command,
            exit_code=job.exit_code,
            stdout="".join(job.output_buffer),
            stderr="",
            duration_seconds=round(time.perf_counter() - start, 2),
            timed_out=False,
        )

    def open_terminal_window(
        self,
        directory: str | Path,
        command: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Launch an interactive Windows Terminal window in the designated directory and optionally execute a command."""
        target_dir = Path(directory).resolve()
        if not target_dir.is_dir():
            return {
                "success": False,
                "error": f"Directory does not exist: {directory}",
            }

        import shutil
        wt_path = shutil.which("wt.exe") or shutil.which("wt")

        clean_dir = str(target_dir).replace('"', '`"')
        if wt_path:
            if command:
                escaped_cmd = command.replace('"', '`"')
                cmd_line = f'wt.exe -d "{clean_dir}" powershell.exe -NoExit -Command "{escaped_cmd}"'
            else:
                cmd_line = f'wt.exe -d "{clean_dir}"'
        else:
            if command:
                escaped_cmd = command.replace('"', '`"')
                cmd_line = f'powershell.exe -NoExit -Command "Set-Location \'{clean_dir}\'; {escaped_cmd}"'
            else:
                cmd_line = f'powershell.exe -NoExit -Command "Set-Location \'{clean_dir}\'"'

        try:
            subprocess.Popen(f"start {cmd_line}", shell=True)
            logger.info("Launched terminal in %s (cmd: %s)", target_dir, command)

            # Wait briefly and find the newly active/focused terminal window
            time.sleep(0.7)
            from app.tools.desktop.window_manager import window_manager
            win = window_manager.find_window("terminal 1")

            return {
                "success": True,
                "directory": str(target_dir),
                "command_executed": command,
                "window": win.alias if win else "terminal 1",
                "hwnd": win.hwnd if win else None,
                "title": win.title if win else "Windows Terminal",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# Global terminal singleton
terminal_manager = TerminalManager()
