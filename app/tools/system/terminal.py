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


# Global terminal singleton
terminal_manager = TerminalManager()
