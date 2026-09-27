"""Unified Supervisor for Voice Desktop OS.

Starts and coordinates all system components together in a single command:
1. FastAPI Server (REST & WebSocket telemetry)
2. LiveKit Voice Agent Worker (Gemini Live speech-to-speech engine)
3. Local Microphone & Speaker Audio Pipeline
4. Interactive Rich CLI Console for visual feedback and text fallback
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.config import ROOT_DIR, settings
from app.tools.desktop.window_manager import window_manager
from app.voice.local_audio import local_voice_client
from app.workers.orchestrator import orchestrator

console = Console()
logger = logging.getLogger(__name__)


class AllInOneSupervisor:
    """Coordinates FastAPI, LiveKit voice worker, local audio, and CLI console."""

    def __init__(self):
        self.server: uvicorn.Server | None = None
        self.server_thread: threading.Thread | None = None
        self.worker_process: subprocess.Popen | None = None
        self._worker_log: Any = None
        self.room_name: str | None = None
        self._shutdown_event = threading.Event()
        self.actual_port: int = settings.server_port

    def _find_available_port(self, host: str, start_port: int) -> int:
        """Find an available port if start_port is occupied by another application."""
        import socket
        port = start_port
        while port < start_port + 50:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                if s.connect_ex((host, port)) != 0:
                    return port
                port += 1
        return start_port

    def start_fastapi(self):
        """Run FastAPI server in a background daemon thread."""
        actual_port = self._find_available_port(settings.server_host, settings.server_port)
        if actual_port != settings.server_port:
            console.print(
                f"[bold yellow][!] Port {settings.server_port} is in use by another app. "
                f"Voice Desktop OS automatically assigned to port {actual_port}.[/bold yellow]"
            )
        self.actual_port = actual_port
        os.environ["SERVER_PORT"] = str(actual_port)
        config = uvicorn.Config(
            "app.main:app",
            host=settings.server_host,
            port=actual_port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()

    def start_voice_worker(self):
        """Spawn the LiveKit Voice Agent worker as a managed background process."""
        logs_dir = ROOT_DIR / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._worker_log = open(logs_dir / "voice_worker.log", "w", encoding="utf-8")
        self.worker_process = subprocess.Popen(
            [sys.executable, "-m", "app.voice.agent", "start"],
            stdout=self._worker_log,
            stderr=self._worker_log,
            cwd=str(ROOT_DIR),
        )

    def stop_all(self):
        """Clean shutdown of all background services."""
        console.print("\n[yellow]Shutting down Voice Desktop OS services...[/yellow]")
        self._shutdown_event.set()

        # Stop local mic audio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(local_voice_client.disconnect())
            else:
                loop.run_until_complete(local_voice_client.disconnect())
        except Exception:
            pass

        # Stop voice worker process
        if self.worker_process and self.worker_process.poll() is None:
            try:
                self.worker_process.terminate()
                self.worker_process.wait(timeout=3)
            except Exception:
                try:
                    self.worker_process.kill()
                except Exception:
                    pass

        if self._worker_log and not self._worker_log.closed:
            try:
                self._worker_log.close()
            except Exception:
                pass

        # Stop FastAPI server
        if self.server:
            self.server.should_exit = True

        console.print("[green]All services cleanly stopped. Goodbye![/green]")

    async def run(self, open_browser: bool = True, enable_cli_mic: bool = False):
        """Start all services, launch the Web UI, and manage the supervisor process."""
        # 1. Start FastAPI server (resolves actual_port)
        self.start_fastapi()

        web_url = f"http://{settings.server_host}:{self.actual_port}"

        # 2. Print Startup Banner
        banner = Text()
        banner.append(">> VOICE DESKTOP OS - WEB INTERFACE RUNNER\n", style="bold cyan")
        banner.append("Running dual-agent desktop stack (Gemini Live Speech + TypeSafe Jev):\n\n", style="bold white")
        banner.append(" [1] Web Application:     ", style="dim")
        banner.append(f"{web_url} (Active)\n", style="bold green")
        banner.append(" [2] Voice & Vision Agent:", style="dim")
        banner.append(f" LiveKit + {settings.gemini_live_model} (Active)\n", style="green")
        banner.append(" [3] System One Reflex:   ", style="dim")
        banner.append(f"TypeSafe Jev ({settings.jev_model}) (Active)\n", style="green")
        banner.append(" [4] Web Audio / Mic:     ", style="dim")
        banner.append("Browser WebRTC + LiveKit Real-Time Stream (Ready)", style="green")
        console.print(Panel(banner, border_style="cyan", box=box.ROUNDED))

        # 3. Start LiveKit Voice Worker
        self.start_voice_worker()

        # 4. Optional CLI Microphone (Browser handles mic by default)
        if enable_cli_mic:
            await asyncio.sleep(2.0)
            try:
                with console.status("[bold cyan]Connecting CLI microphone to voice agent...[/bold cyan]"):
                    self.room_name = await local_voice_client.connect()
                    console.print(f"[bold green]Connected to Voice Room: {self.room_name}[/bold green]")
                    console.print("[dim][Mic] Microphone active: You can speak aloud to control your PC anytime![/dim]\n")
            except Exception as exc:
                console.print(f"[yellow]Note: CLI mic streaming not started ({exc}). Continuing in web mode.[/yellow]\n")

        # 5. Open Web UI in browser
        if open_browser:
            await asyncio.sleep(0.8)
            console.print(f"[bold cyan]--> Opening Web UI in browser:[/bold cyan] [underline green]{web_url}[/underline green]\n")
            try:
                import webbrowser
                webbrowser.open(web_url)
            except Exception:
                pass

        # 6. Interactive Terminal in foreground for monitoring and control
        active_win = window_manager.get_active_window()
        active_title = active_win.title if active_win else "None"
        console.print(f"[dim]Foreground Window:[/dim] [bold white]{active_title}[/bold white]")
        console.print("[dim]Use the Web UI in your browser or type commands below (/help for shortcuts, /exit to quit):[/dim]\n")

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Run input in executor to avoid blocking asyncio loop
                    loop = asyncio.get_running_loop()
                    user_input = await loop.run_in_executor(
                        None, lambda: console.input("[bold cyan]VoiceDesktop > [/bold cyan]")
                    )
                    user_input = user_input.strip()
                except (KeyboardInterrupt, EOFError):
                    break

                if not user_input:
                    continue

                cmd_lower = user_input.lower()
                if cmd_lower in ("/exit", "/quit", "exit", "quit"):
                    break

                if cmd_lower == "/help":
                    console.print(
                        Panel(
                            "• [cyan]Speak into mic[/cyan] : Just talk naturally to control your PC\n"
                            "• [cyan]/windows[/cyan]       : View all open desktop windows and HWNDs\n"
                            "• [cyan]/focus <name>[/cyan]    : Bring window to front\n"
                            "• [cyan]/screenshot[/cyan]    : Save primary monitor to screenshot.png\n"
                            "• [cyan]/status[/cyan]        : Refresh current foreground window\n"
                            "• [cyan]/exit[/cyan]          : Stop all services and quit",
                            title="Voice Desktop OS Commands",
                            border_style="dim",
                        )
                    )
                    continue

                if cmd_lower == "/windows":
                    windows = window_manager.list_windows()
                    table = Table(title="Open Application Windows", box=box.SIMPLE_HEAVY)
                    table.add_column("HWND", style="cyan")
                    table.add_column("Process", style="green")
                    table.add_column("Title", style="white")
                    for w in windows:
                        table.add_row(str(w.hwnd), w.process_name, w.title[:55])
                    console.print(table)
                    continue

                if cmd_lower.startswith("/focus "):
                    q = user_input[7:].strip()
                    if window_manager.bring_to_front(q):
                        console.print(f"[green]Focused '{q}'[/green]")
                    else:
                        console.print(f"[red]Could not find '{q}'[/red]")
                    continue

                # Execute typed command through Master Orchestrator
                with console.status("[bold green]Evaluating via Jev System One...[/bold green]"):
                    result = await orchestrator.dispatch(user_input)

                tier = result.get("tier", "unknown")
                duration = result.get("duration_seconds", 0.0)
                action = result.get("action", "")
                message = result.get("message", "")

                tier_badge = (
                    "[bold green]System One (Jev Reflex)[/bold green]"
                    if tier == "system_one_reflex"
                    else "[bold cyan]Speech Model (Gemini Live)[/bold cyan]"
                )

                card = Text()
                card.append(f"Result: ", style="bold white")
                card.append(f"{message}\n\n", style="white")
                card.append(f"Tier: ", style="dim")
                card.append(f"{tier_badge}  ")
                card.append(f"Action: ", style="dim")
                card.append(f"{action}  ", style="cyan")
                card.append(f"Latency: ", style="dim")
                card.append(f"{duration}s", style="magenta")

                border_color = "green" if tier == "system_one_reflex" else "cyan"
                console.print(Panel(card, border_style=border_color, box=box.ROUNDED))

        finally:
            self.stop_all()


# Global supervisor instance
supervisor = AllInOneSupervisor()
