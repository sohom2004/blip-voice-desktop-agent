"""Top-level entrypoint for Voice Desktop OS.

Usage:
  python run.py          # Start interactive CLI session (default)
  python run.py chat     # Start interactive CLI session
  python run.py server   # Start FastAPI REST & WebSocket server
  python run.py worker   # Start LiveKit voice agent background worker
  python run.py mic      # Stream local microphone/speakers to voice room
"""

import os
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles to prevent cp1252 charmap encoding errors
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Enable Windows Per-Monitor DPI Awareness v2 early to align UIA, MSS capture, and PyAutoGUI coordinates
from app.tools.desktop.dpi import enable_per_monitor_dpi_awareness
enable_per_monitor_dpi_awareness()

from app.cli.console import cli_app

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("start")
    cli_app()
