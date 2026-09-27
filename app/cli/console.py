"""Rich Interactive CLI Console for Voice Desktop Application.

Provides a unified command-line experience with:
- Interactive text and voice command prompt
- Real-time desktop status and window inspector
- Live telemetry from Jev System One and System Two LLM Worker
- Subcommands to launch the FastAPI server, LiveKit worker, or microphone client
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.config import settings
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.window_manager import window_manager
from app.voice.agent import run_agent as start_voice_agent_worker
from app.voice.local_audio import local_voice_client
from app.workers.orchestrator import orchestrator

console = Console()
cli_app = typer.Typer(help="Voice Desktop OS: Jev Reflex & Multimodal Assistant")


def print_banner():
    banner = Text()
    banner.append("⚡ VOICE DESKTOP OS\n", style="bold cyan")
    banner.append("Dual-Agent Desktop Automation (Gemini Live Speech + TypeSafe Jev)\n", style="bold white")
    banner.append("• System One (Reflex): ", style="dim")
    banner.append(f"TypeSafe Jev ({settings.jev_model})\n", style="green")
    banner.append("• Voice, Vision & Reasoning: ", style="dim")
    banner.append(f"LiveKit + {settings.gemini_live_model}", style="cyan")

    console.print(Panel(banner, border_style="cyan", box=box.ROUNDED))


def display_windows_table():
    windows = window_manager.list_windows()
    table = Table(title="Active Application Windows", box=box.SIMPLE_HEAVY)
    table.add_column("HWND", style="cyan", no_wrap=True)
    table.add_column("Process", style="green")
    table.add_column("Title", style="white")
    table.add_column("Status", style="yellow")

    for w in windows:
        status = "[bold green]ACTIVE[/bold green]" if w.is_active else "background"
        table.add_row(str(w.hwnd), w.process_name, w.title[:60], status)

    console.print(table)


async def run_interactive_loop():
    print_banner()

    active_win = window_manager.get_active_window()
    active_title = active_win.title if active_win else "None"
    console.print(f"[dim]Foreground Window:[/dim] [bold white]{active_title}[/bold white]\n")
    console.print("[dim]Type a command (or '/help' for shortcuts, '/exit' to quit):[/dim]")

    while True:
        try:
            user_input = console.input("[bold cyan]VoiceDesktop > [/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Exiting Voice Desktop.[/yellow]")
            break

        if not user_input:
            continue

        cmd_lower = user_input.lower()
        if cmd_lower in ("/exit", "/quit", "exit", "quit"):
            console.print("[yellow]Exiting Voice Desktop.[/yellow]")
            break

        if cmd_lower == "/help":
            console.print(
                Panel(
                    "• [cyan]/windows[/cyan]    : List open windows with HWNDs\n"
                    "• [cyan]/focus <name>[/cyan] : Bring window to front\n"
                    "• [cyan]/screenshot[/cyan] : Capture screen to screenshot.png\n"
                    "• [cyan]/status[/cyan]     : Show current foreground window\n"
                    "• [cyan]/exit[/cyan]       : Quit",
                    title="Available Shortcuts",
                    border_style="dim",
                )
            )
            continue

        if cmd_lower == "/windows":
            display_windows_table()
            continue

        if cmd_lower.startswith("/focus "):
            query = user_input[7:].strip()
            success = window_manager.bring_to_front(query)
            if success:
                console.print(f"[green]Focused window matching '{query}'[/green]")
            else:
                console.print(f"[red]Could not find or focus window matching '{query}'[/red]")
            continue

        if cmd_lower == "/screenshot":
            path = screen_capture.capture_to_file("screenshot.png")
            console.print(f"[green]Saved screenshot to {path.resolve()}[/green]")
            continue

        if cmd_lower == "/status":
            win = window_manager.get_active_window()
            console.print(f"Foreground: [bold]{win.title if win else 'None'}[/bold]")
            continue

        # Execute through Master Orchestrator
        with console.status("[bold green]Evaluating via Jev System One...[/bold green]"):
            result = await orchestrator.dispatch(user_input)

        tier = result.get("tier", "unknown")
        duration = result.get("duration_seconds", 0.0)
        action = result.get("action", "")
        message = result.get("message", "")

        # Format Result Card
        if tier == "system_one_reflex":
            tier_badge = "[bold green]System One (Jev Reflex)[/bold green]"
            border = "green"
        else:
            tier_badge = "[bold cyan]Speech Model (Gemini Live)[/bold cyan]"
            border = "cyan"

        card = Text()
        card.append(f"Result: ", style="bold white")
        card.append(f"{message}\n\n", style="white")
        card.append(f"Tier: ", style="dim")
        card.append(f"{tier_badge}  ")
        card.append(f"Action: ", style="dim")
        card.append(f"{action}  ", style="cyan")
        card.append(f"Latency: ", style="dim")
        card.append(f"{duration}s", style="magenta")

        console.print(Panel(card, border_style=border, box=box.ROUNDED))


@cli_app.command(name="start", help="Start all services (Web UI, FastAPI Server, and LiveKit Voice Worker)")
def cli_start(
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not automatically open default browser"),
    cli_mic: bool = typer.Option(False, "--cli-mic", help="Also capture microphone directly in the CLI terminal"),
):
    from app.cli.supervisor import supervisor
    asyncio.run(supervisor.run(open_browser=not no_browser, enable_cli_mic=cli_mic))


@cli_app.command(name="chat", help="Start interactive CLI desktop automation session (standalone)")
def cli_chat():
    asyncio.run(run_interactive_loop())


@cli_app.command(name="server", help="Start the FastAPI backend server")
def cli_server(
    host: str = settings.server_host,
    port: int = settings.server_port,
    reload: bool = False,
):
    import uvicorn
    console.print(f"[bold green]Starting FastAPI server at http://{host}:{port}[/bold green]")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


@cli_app.command(name="worker", help="Start the background LiveKit speech-to-speech voice worker")
def cli_worker(
    mode: str = typer.Argument("start", help="LiveKit worker mode: 'start' or 'dev'"),
):
    console.print(f"[bold magenta]Starting LiveKit Voice Agent Worker ({mode})...[/bold magenta]")
    sys.argv = [sys.argv[0], mode]
    from app.voice.agent import run_agent
    run_agent()


@cli_app.command(name="mic", help="Connect microphone and speaker audio to LiveKit voice room")
def cli_mic(room: Optional[str] = typer.Option(None, help="LiveKit room name to connect to")):
    async def _mic_runner():
        console.print("[bold cyan]Connecting local microphone and speakers to LiveKit room...[/bold cyan]")
        assigned_room = await local_voice_client.connect(room_name=room)
        console.print(f"[bold green]Connected to room: {assigned_room}[/bold green]")
        console.print("[dim]Speak into your microphone. Press Ctrl+C to disconnect.[/dim]")
        try:
            while True:
                await asyncio.sleep(1.0)
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[yellow]Disconnecting audio...[/yellow]")
            await local_voice_client.disconnect()

    asyncio.run(_mic_runner())


if __name__ == "__main__":
    cli_app()
