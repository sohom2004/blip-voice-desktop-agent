"""Real-Time Speech-to-Speech Voice Agent for Desktop Automation.

Powered by LiveKit Agents and Google Gemini Live (gemini-3.1-flash-live-preview).
- Provides low-latency duplex audio streaming with barge-in interruption tuning.
- Directly executes complex reasoning, multimodal vision grounding, and desktop automation tools.
- Collaborates with TypeSafe Jev (System One) for sub-second simple navigation reflexes.
- Capable of capturing and inspecting desktop screenshots whenever needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import Any

from google.genai import types as genai_types
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobExecutorType,
    TurnHandlingOptions,
    cli,
    function_tool,
)
from livekit.agents.voice.events import ToolCallStarted, ToolCallEnded
from livekit.plugins import google

from app.config import settings
from app.tools.browser.browser_manager import browser_manager
from app.tools.desktop.mouse_keyboard import mouse_keyboard
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.web_browser import open_url_or_search as _browser_open_url
from app.tools.desktop.window_manager import window_manager
from app.tools.system.file_ops import file_manager
from app.tools.system.terminal import terminal_manager
from app.voice.events import get_active_room, publish_event, set_active_room
from app.workers.vision import vision_engine

logger = logging.getLogger(__name__)


def _extract_item_text(item: Any) -> str:
    """Extract string text from a ChatMessage, AgentHandoff, or generic chat item."""
    if item is None:
        return ""
    text = getattr(item, "text_content", None) or getattr(item, "raw_text_content", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    content = getattr(item, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str) and part.strip():
                parts.append(part.strip())
            elif hasattr(part, "text") and getattr(part, "text", None):
                parts.append(str(part.text).strip())
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]).strip())
        if parts:
            return " ".join(parts).strip()

    return ""


def _format_tool_activity_text(tool_name: str, arguments: Any) -> str:
    """Produce human-readable activity text for tool starts."""
    args_dict = {}
    if isinstance(arguments, str) and arguments.strip():
        try:
            args_dict = json.loads(arguments)
        except Exception:
            args_dict = {"arg": arguments}
    elif isinstance(arguments, dict):
        args_dict = arguments

    if tool_name == "open_application":
        app = args_dict.get("app_name") or args_dict.get("name") or ""
        return f"Opening application: {app}" if app else "Opening application..."
    elif tool_name == "focus_window":
        query = args_dict.get("query") or args_dict.get("title") or ""
        return f"Focusing window: {query}" if query else "Focusing window..."
    elif tool_name == "list_open_windows":
        return "Listing open windows..."
    elif tool_name == "open_url_or_search":
        target = args_dict.get("query_or_url") or args_dict.get("url") or ""
        return f"Opening URL / search: {target}" if target else "Browsing web..."
    elif tool_name == "click_mouse":
        x = args_dict.get("x")
        y = args_dict.get("y")
        btn = args_dict.get("button", "left")
        if x is not None and y is not None:
            return f"Clicking at ({x}, {y}) [{btn}]"
        return "Clicking mouse..."
    elif tool_name == "type_text":
        txt = args_dict.get("text", "")
        preview = (txt[:25] + "...") if len(txt) > 25 else txt
        return f'Typing: "{preview}"' if preview else "Typing text..."
    elif tool_name == "press_hotkey":
        keys = args_dict.get("keys", "")
        return f"Pressing hotkey: {keys}" if keys else "Pressing hotkey..."
    elif tool_name == "scroll_screen":
        direction = args_dict.get("direction", "down")
        amt = args_dict.get("amount", 5)
        return f"Scrolling {direction} ({amt} clicks)"
    elif tool_name == "execute_terminal_command":
        cmd = args_dict.get("command", "")
        preview = (cmd[:35] + "...") if len(cmd) > 35 else cmd
        return f"Running terminal command: {preview}" if preview else "Executing command..."
    elif tool_name == "read_file":
        path = args_dict.get("path", "")
        return f"Reading file: {os.path.basename(path) if path else 'file'}"
    elif tool_name == "write_file":
        path = args_dict.get("path", "")
        return f"Writing file: {os.path.basename(path) if path else 'file'}"
    elif tool_name == "patch_file":
        path = args_dict.get("path", "")
        return f"Patching file: {os.path.basename(path) if path else 'file'}"
    elif tool_name == "ask_jev_reflex":
        cmd = args_dict.get("command", "")
        return f"Jev reflex: {cmd}" if cmd else "Executing Jev reflex..."
    elif tool_name == "inspect_desktop_screen":
        return "Inspecting desktop screen..."
    elif tool_name == "read_terminal_output":
        return "Reading terminal output..."
    elif tool_name == "read_browser_content":
        return "Reading browser page content..."
    elif tool_name == "wait_for_condition":
        cond = args_dict.get("condition_type", "")
        tgt = args_dict.get("target", "")
        return f"Waiting for {cond} ({tgt})..." if cond else "Waiting for condition..."
    elif tool_name == "await_background_task":
        jid = args_dict.get("job_id", "")
        return f"Awaiting background job {jid}..." if jid else "Waiting for background task..."
    else:
        if args_dict:
            first_val = next(iter(args_dict.values()))
            return f"{tool_name}: {first_val}"
        return f"Running {tool_name}..."


def _format_tool_result_text(tool_name: str, status: str, message: Any) -> str:
    """Produce clean summary text for tool completions."""
    if not message:
        return f"{tool_name} completed ({status})"
    msg_str = str(message).strip()
    first_line = msg_str.split("\n")[0].strip()
    if len(first_line) > 100:
        first_line = first_line[:97] + "..."
    return first_line if first_line else f"{tool_name} completed ({status})"


# Global reference to active Gemini Live session
_active_session: AgentSession | None = None


def get_active_session() -> AgentSession | None:
    """Return the currently connected Gemini Live AgentSession, if any."""
    return _active_session


SYSTEM_PROMPT = """You are an advanced voice assistant and primary desktop automation agent controlling a Windows PC.
You speak naturally with the user and have complete control of their computer through your desktop tools.

Architecture & Operating Rules:
1. You (Gemini Live speech-to-speech) perform all complex tasks, planning, coding, file operations, web exploration, and visual desktop grounding.
2. Jev (System One) handles fast, high-confidence navigation and simple reflexes, and delegates complex tasks to you.
3. You can ALWAYS call `inspect_desktop_screen` to see the screen, verify results, or locate interactive buttons and input fields with pixel coordinates whenever you are unsure or stuck.
4. For simple window switching, app launching, or quick desktop actions, you can also call `ask_jev_reflex`.

CRITICAL ANTI-HALLUCINATION & TASK SYNCHRONIZATION RULES:
1. NEVER declare or hallucinate that a task is completed (e.g., claiming "I've opened the app", "The command is done", or "The page loaded") before the action has actually finished and verified!
2. When launching an app or opening a page, verify the window appeared or call `wait_for_condition(condition_type='window_open', target=...)`.
3. When running terminal builds, scripts, or tests, do NOT claim it finished until you see the exit code or prompt. Use `await_background_task` or `read_terminal_output` to check status.
4. If a process, page, or build is still loading or running, give brief intermediate status ("Still loading...", "Running the build...", "Waiting for the window...") instead of prematurely ending the turn.
5. If an action fails or times out, truthfully inform the user instead of pretending it succeeded.

Available Tools:
- `inspect_desktop_screen`: Capture and inspect the screen, including terminal text buffers, browser web pages, and UI controls.
- `read_terminal_output`: Read visible text, prompts, errors, and commands from the active terminal window or background jobs.
- `read_browser_content`: Read web page content, title, URL, and interactive elements from the active browser window.
- `wait_for_condition`: Wait for a window to open/close, browser ready, or brief stabilization delay.
- `await_background_task`: Wait for a background terminal job to complete and verify its exit code.
- `focus_window`: Bring any application window to front by title or process name.
- `list_open_windows`: List all currently open desktop windows.
- `open_application`: Launch any app (e.g. calculator, notepad, chrome, code, spotify).
- `open_url_or_search`: Open a website URL or perform a web search in the default browser.
- `click_mouse`: Click at exact (x, y) screen coordinates.
- `type_text`: Type text into the focused control or active window.
- `press_hotkey`: Keyboard shortcuts (ctrl+s, ctrl+c, ctrl+v, alt+tab, enter, etc.).
- `scroll_screen`: Scroll mouse wheel up or down.
- `execute_terminal_command`: Run PowerShell commands and inspect terminal output.
- `read_file`, `write_file`, `patch_file`: Inspect, write, or modify files.
- `ask_jev_reflex`: Ask Jev System One to quickly perform simple navigation / desktop reflexes.

Spoken Guidelines:
- Acknowledge actions quickly and naturally (e.g. "Looking at your screen now...", "Opening YouTube for you", "I'll run that command").
- Keep conversational fillers moderate and brief—natural and polite without being repetitive or rambling.
- Perform necessary tool actions step-by-step.
- When finished, give a concise, friendly spoken confirmation summarizing the verified outcome.
- Never read raw code blocks, long stack traces, or raw JSON data aloud unless specifically asked.
"""


# ---------------------------------------------------------------------------
# Direct Desktop Automation Tools for Gemini Live Speech Model
# ---------------------------------------------------------------------------

@function_tool
async def inspect_desktop_screen(focus_hint: str = "") -> str:
    """Capture and inspect the current desktop screen.
    Call this whenever you need to see what is on the screen, locate interactive controls/buttons,
    read terminal outputs, examine browser web pages, find coordinates to click, or when a task is stuck.
    Returns the active window title, screen resolution, visible interactive elements with coordinates,
    and interior dynamic content (terminal text buffers, browser DOM/page content, or documents)."""
    logger.info("Gemini Live calling inspect_desktop_screen (hint: %s)", focus_hint)
    try:
        active_win = window_manager.get_active_window()
        active_title = active_win.title if active_win else "Unknown"
        active_proc = active_win.process_name if active_win else "Unknown"

        annotated_img, tags = vision_engine.get_annotated_screenshot(max_marks=30)
        try:
            annotated_img.save("screenshot.png")
        except Exception:
            pass

        lines = [
            f"Screen inspected: {active_title[:40]} ({annotated_img.width}x{annotated_img.height})",
            f"Active Window: '{active_title}' (Process: {active_proc})",
        ]

        # Extract interior content (Terminal text buffer or Browser page content)
        content_info = await vision_engine.inspect_active_content(active_win, annotated_img, focus_hint=focus_hint)
        if content_info.get("text"):
            source = content_info.get("source", "Visual Inspection")
            lines.append(f"\n--- Interior Content ({source}) ---")
            lines.append(content_info["text"][:1800])
            lines.append("--- End Interior Content ---\n")

        cdp_elements = content_info.get("dom_elements", [])
        if cdp_elements:
            lines.append("Browser In-Page Elements (CDP):")
            for el in cdp_elements[:15]:
                lines.append(f" - [DOM] {el.get('role', 'element')}: '{el.get('text', '')[:40]}' selector={el.get('selector', '')}")

        lines.append("Visible Interactive Controls:")
        for t in tags[:20]:
            lines.append(f" - [Tag {t['tag']}] {t['type']}: '{t['name']}' at center coordinates {t['center']}")

        return "\n".join(lines)

    except Exception as exc:
        logger.error("Error inspecting screen: %s", exc)
        return f"Error capturing screen: {exc}"


@function_tool
async def read_terminal_output(lines: int = 35) -> str:
    """Read the visible text buffer, recent commands, errors, and output from the active terminal window or background jobs.
    Use this whenever you need to check command results, compiler errors, or shell status."""
    logger.info("Gemini Live calling read_terminal_output")
    recent_jobs = terminal_manager.background_jobs
    job_outputs = []
    for jid, job in list(recent_jobs.items())[-3:]:
        job_outputs.append(f"Job {jid} ('{job.command}'): finished={job.is_finished} exit={job.exit_code}\n{''.join(job.output_buffer[-lines:])}")

    active_win = window_manager.get_active_window()
    proc = active_win.process_name.lower() if active_win else ""
    is_terminal = proc in vision_engine.TERMINAL_PROCESSES or "terminal" in proc or "powershell" in proc or "pwsh" in proc or "cmd" in proc

    if is_terminal:
        img = screen_capture.capture_primary_monitor()
        terminal_text = await vision_engine.extract_visual_content(img, window_type="terminal")
        res = f"Active Terminal Window: '{active_win.title}' (Process: {proc})\nVisible Buffer:\n{terminal_text}"
        if job_outputs:
            res += "\n\nBackground Jobs Output:\n" + "\n".join(job_outputs)
        return res

    if job_outputs:
        return "Background Jobs Output:\n" + "\n".join(job_outputs)

    # If terminal is not active, try to find and inspect it
    term = window_manager.find_window("terminal") or window_manager.find_window("powershell") or window_manager.find_window("cmd")
    if term:
        window_manager.bring_to_front(term.hwnd)
        import asyncio
        await asyncio.sleep(0.3)
        img = screen_capture.capture_primary_monitor()
        terminal_text = await vision_engine.extract_visual_content(img, window_type="terminal")
        return f"Switched to Terminal: '{term.title}'\nVisible Buffer:\n{terminal_text}"

    return f"Active window '{active_win.title if active_win else 'None'}' is not a terminal, and no background jobs found."


@function_tool
async def read_browser_content() -> str:
    """Read the current web page content, title, URL, visible text, and interactive elements from the browser.
    Use this to read articles, search results, forms, dialogs, or documentation in Chrome/Brave/Edge."""
    logger.info("Gemini Live calling read_browser_content")
    active_win = window_manager.get_active_window()

    # Try CDP extraction first
    cdp_content = await browser_manager.extract_page_content()
    if cdp_content and cdp_content.get("text"):
        elements_preview = "\n".join([f" - [DOM] {e.get('role')}: '{e.get('text')[:35]}' ({e.get('selector')})" for e in cdp_content.get("elements", [])[:15]])
        return (
            f"Browser Page (CDP): '{cdp_content.get('title')}'\n"
            f"URL: {cdp_content.get('url')}\n"
            f"Visible Content:\n{cdp_content.get('text')[:2000]}\n\n"
            f"Interactive Elements:\n{elements_preview}"
        )

    # Fallback to visual multimodal extraction
    img = screen_capture.capture_primary_monitor()
    browser_text = await vision_engine.extract_visual_content(img, window_type="browser")
    win_title = active_win.title if active_win else "Browser"
    return f"Browser Window: '{win_title}'\nVisible Content:\n{browser_text}"


@function_tool
async def focus_window(query: str) -> str:
    """Bring an application window to the foreground by title, process name, or HWND."""
    logger.info("Gemini Live calling focus_window: %s", query)
    success = window_manager.bring_to_front(query)
    if success:
        return f"Successfully focused window matching '{query}'."
    return f"Failed to find or focus window matching '{query}'."


@function_tool
async def list_open_windows() -> str:
    """List all currently open, visible application windows on the desktop."""
    windows = window_manager.list_windows()
    if not windows:
        return "No visible windows found."
    lines = [f"- [{w.hwnd}] {w.process_name}: '{w.title}' (active={w.is_active})" for w in windows]
    return "Open Windows:\n" + "\n".join(lines)


@function_tool
async def open_application(app_name: str) -> str:
    """Launch any application or tool on this PC (e.g. 'calc', 'chrome', 'notepad', 'code', 'spotify', 'terminal')."""
    logger.info("Gemini Live calling open_application: %s", app_name)
    success = window_manager.launch_app(app_name)
    if success:
        return f"Successfully launched application: {app_name}"
    return f"Failed to launch application: {app_name}"


@function_tool
async def open_url_or_search(query_or_url: str) -> str:
    """Open a website URL or perform a web search in the user's default browser."""
    logger.info("Gemini Live calling open_url_or_search: %s", query_or_url)
    return _browser_open_url(query_or_url)


@function_tool
async def click_mouse(x: int, y: int, button: str = "left") -> str:
    """Click at screen coordinates (x, y) with specified mouse button ('left' or 'right')."""
    logger.info("Gemini Live calling click_mouse at (%d, %d)", x, y)
    cx, cy = mouse_keyboard.click(x=x, y=y, button=button)
    return f"Clicked mouse at ({cx}, {cy}) with button '{button}'."


@function_tool
async def type_text(text: str) -> str:
    """Type text into the currently active or focused input control."""
    logger.info("Gemini Live calling type_text: len=%d", len(text))
    mouse_keyboard.type_text(text)
    return f"Typed {len(text)} characters into active control."


@function_tool
async def press_hotkey(keys: str) -> str:
    """Press a key combination, e.g. 'ctrl+s', 'ctrl+c', 'ctrl+v', 'alt+tab', 'enter', 'esc'."""
    logger.info("Gemini Live calling press_hotkey: %s", keys)
    key_list = [k.strip().lower() for k in keys.split("+")]
    mouse_keyboard.hotkey(*key_list)
    return f"Pressed hotkey combo: {'+'.join(key_list)}"


@function_tool
async def scroll_screen(direction: str = "down", amount: int = 5) -> str:
    """Scroll mouse wheel ('up' or 'down')."""
    dir_clean = "down" if "down" in direction.lower() else "up"
    mouse_keyboard.scroll(clicks=amount, direction=dir_clean)
    return f"Scrolled {dir_clean} by {amount} clicks."


@function_tool
async def execute_terminal_command(command: str) -> str:
    """Execute a PowerShell command in the terminal and return stdout, stderr, and exit code."""
    logger.info("Gemini Live calling execute_terminal_command: %s", command)
    res = await terminal_manager.execute(command)
    output = res.output
    return f"Exit code {res.exit_code} (took {res.duration_seconds}s):\n{output[:1500]}"


@function_tool
async def read_file(path: str, start_line: int = 1, end_line: int = 100) -> str:
    """Read a slice of lines from a file on this computer."""
    logger.info("Gemini Live calling read_file: %s", path)
    res = file_manager.read_file(path, start_line=start_line, end_line=end_line)
    if res.get("success"):
        return f"File: {path} (lines {res['start_line']}-{res['end_line']} of {res['total_lines']}):\n{res['content']}"
    return f"Error reading file: {res.get('error')}"


@function_tool
async def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with given text content."""
    logger.info("Gemini Live calling write_file: %s", path)
    res = file_manager.write_file(path, content, overwrite=True)
    if res.get("success"):
        return f"Successfully wrote {res['bytes_written']} bytes to {path}."
    return f"Error writing file: {res.get('error')}"


@function_tool
async def patch_file(path: str, target: str, replacement: str) -> str:
    """Replace an exact substring within a file with replacement text."""
    logger.info("Gemini Live calling patch_file: %s", path)
    res = file_manager.patch_file(path, target, replacement)
    if res.get("success"):
        return f"Successfully patched file {path}."
    return f"Patch failed: {res.get('error')}"


@function_tool
async def wait_for_condition(condition_type: str, target: str, timeout: float = 10.0) -> str:
    """Wait for a desktop condition before declaring success or proceeding.
    Supported condition_types:
    - 'window_open': wait until a window with title/process matching 'target' appears.
    - 'window_close': wait until a window matching 'target' is closed.
    - 'browser_ready': wait until active browser window or URL finishes initial load.
    - 'delay' or 'sleep': wait for a specific duration in seconds (specified in 'target').
    """
    logger.info("Gemini Live calling wait_for_condition: type=%s target=%s timeout=%.1f", condition_type, target, timeout)
    cond = condition_type.strip().lower()
    t_out = min(max(float(timeout), 0.5), 30.0)

    if cond in ("window_open", "open", "window"):
        win = window_manager.wait_for_window(target, timeout=t_out)
        if win:
            return f"Verified: Window '{win.title}' (process: {win.process_name}) is open and active."
        return f"Condition check timed out: Window matching '{target}' did not appear within {t_out}s."

    elif cond in ("window_close", "close"):
        closed = window_manager.wait_for_window_close(target, timeout=t_out)
        if closed:
            return f"Verified: Window matching '{target}' has closed."
        return f"Condition check timed out: Window matching '{target}' did not close within {t_out}s."

    elif cond in ("browser_ready", "page_ready"):
        win = window_manager.wait_for_window(target if target else "chrome", timeout=t_out)
        await asyncio.sleep(1.0)
        return f"Verified: Browser window is ready."

    elif cond in ("delay", "sleep"):
        try:
            sec = min(float(target), t_out)
        except Exception:
            sec = min(2.0, t_out)
        await asyncio.sleep(sec)
        return f"Waited {sec:.1f}s for desktop/application state to stabilize."

    return f"Unknown condition_type '{condition_type}'. Supported: 'window_open', 'window_close', 'browser_ready', 'delay'."


@function_tool
async def await_background_task(job_id: str, timeout: float = 30.0) -> str:
    """Wait for an ongoing background terminal job or build to complete and return its result.
    Use this to prevent announcing completion prematurely while a build or script is still executing.
    """
    logger.info("Gemini Live calling await_background_task: job_id=%s timeout=%.1f", job_id, timeout)
    t_out = min(max(float(timeout), 1.0), 60.0)
    res = await terminal_manager.await_job(job_id, timeout=t_out)
    if res.timed_out:
        return f"Job '{job_id}' is STILL RUNNING after {t_out}s. Do NOT claim the task is done yet.\nRecent output:\n{res.stdout[-500:]}"
    status_str = "SUCCEEDED" if res.exit_code == 0 else f"FAILED (exit code {res.exit_code})"
    return f"Job '{job_id}' {status_str} in {res.duration_seconds}s.\nOutput:\n{res.output[:1200]}"


@function_tool
async def ask_jev_reflex(command: str) -> str:
    """Ask Jev System One to quickly execute a simple desktop action (e.g. switch window, launch app, basic hotkey) in ~150ms."""
    logger.info("Gemini Live delegating simple reflex to Jev: %s", command)
    from app.jev.router import jev_router
    res = await jev_router.execute_reflex(command)
    return res.get("message", "Executed reflex.")


# List of all tools passed directly to the Gemini Live speech model
SPEECH_MODEL_TOOLS = [
    inspect_desktop_screen,
    read_terminal_output,
    read_browser_content,
    wait_for_condition,
    await_background_task,
    focus_window,
    list_open_windows,
    open_application,
    open_url_or_search,
    click_mouse,
    type_text,
    press_hotkey,
    scroll_screen,
    execute_terminal_command,
    read_file,
    write_file,
    patch_file,
    ask_jev_reflex,
]


async def delegate_to_speech_model(task_instruction: str) -> dict[str, Any]:
    """Execute a complex task escalated by Jev using the System Two LLM worker."""
    from app.workers.llm_worker import llm_worker
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: llm_worker.execute_task(task_instruction))
    return {
        "status": "success",
        "tier": "system_two_reasoning",
        "message": res.get("summary", "Task executed."),
    }


# ---------------------------------------------------------------------------
# LiveKit Server & Entrypoint
# ---------------------------------------------------------------------------

server = AgentServer(
    load_threshold=math.inf,
    job_executor_type=JobExecutorType.THREAD,
    num_idle_processes=1,
)


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    """Entrypoint invoked whenever a voice call session connects."""
    global _active_session
    await ctx.connect()
    set_active_room(ctx.room)
    logger.info("Voice agent session connected to room: %s", getattr(ctx.room, "name", "unknown"))

    if not settings.gemini_api_key and not settings.google_api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required for Gemini Live.")

    # Gemini Live barge-in tuning (extracted from Voice-Agent-Demo)
    session = AgentSession(
        turn_handling=TurnHandlingOptions(
            turn_detection="realtime_llm",
            interruption={
                "enabled": True,
                "min_duration": 0.28,
                "min_words": 0,
                "resume_false_interruption": False,
                "false_interruption_timeout": 1.0,
                "backchannel_boundary": None,
            },
        ),
        llm=google.realtime.RealtimeModel(
            model=settings.gemini_live_model,
            voice=settings.gemini_live_voice,
            api_key=settings.gemini_api_key or settings.google_api_key,
            instructions=SYSTEM_PROMPT,
            realtime_input_config=genai_types.RealtimeInputConfig(
                activity_handling=genai_types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=10,
                    silence_duration_ms=450,
                ),
            ),
        ),
    )

    _active_session = session

    agent = Agent(
        instructions=SYSTEM_PROMPT,
        tools=SPEECH_MODEL_TOOLS,
    )

    async def publish_session_event(payload: dict[str, Any]) -> bool:
        return await publish_event(payload, room=ctx.room)

    last_user_transcript = ""
    last_agent_transcript = ""
    active_tools: dict[str, str] = {}

    def _on_user_input_transcribed(ev: Any):
        nonlocal last_user_transcript
        transcript = (getattr(ev, "transcript", None) or "").strip()
        if not transcript:
            return
        is_final = bool(getattr(ev, "is_final", True))
        if is_final:
            last_user_transcript = transcript
        asyncio.create_task(
            publish_session_event(
                {
                    "type": "user_transcript",
                    "text": transcript,
                    "final": is_final,
                }
            )
        )

    def _on_conversation_item_added(ev: Any):
        nonlocal last_user_transcript, last_agent_transcript
        item = getattr(ev, "item", None)
        if item is None:
            return
        role = str(getattr(item, "role", "")).lower()
        text = _extract_item_text(item)
        if not text:
            return

        if "user" in role:
            if text != last_user_transcript:
                last_user_transcript = text
                asyncio.create_task(
                    publish_session_event(
                        {
                            "type": "user_transcript",
                            "text": text,
                            "final": True,
                        }
                    )
                )
        elif "assistant" in role or "model" in role:
            if text != last_agent_transcript:
                last_agent_transcript = text
                asyncio.create_task(
                    publish_session_event(
                        {
                            "type": "agent_transcript",
                            "text": text,
                            "final": True,
                        }
                    )
                )

    def _on_tool_execution_updated(ev: Any):
        update = getattr(ev, "update", None)
        if update is None:
            return

        update_type = getattr(update, "type", None)
        if update_type == "tool_call_started" or isinstance(update, ToolCallStarted):
            fn_call = getattr(update, "function_call", None)
            tool_name = getattr(fn_call, "name", "tool") if fn_call else "tool"
            call_id = getattr(fn_call, "call_id", None) or getattr(update, "call_id", "")
            if call_id:
                active_tools[call_id] = tool_name

            raw_args = getattr(fn_call, "arguments", "")
            action_desc = _format_tool_activity_text(tool_name, raw_args)

            logger.info("Tool execution started: %s (%s)", tool_name, action_desc)
            asyncio.create_task(
                publish_session_event({
                    "type": "model_activity",
                    "phase": "start",
                    "tool": tool_name,
                    "text": action_desc,
                })
            )
        elif update_type == "tool_call_ended" or isinstance(update, ToolCallEnded):
            call_id = getattr(update, "call_id", "")
            tool_name = active_tools.pop(call_id, "tool")
            status = getattr(update, "status", "done")
            message = getattr(update, "message", None)
            summary = _format_tool_result_text(tool_name, status, message)

            logger.info("Tool execution ended: %s (%s)", tool_name, summary)
            asyncio.create_task(
                publish_session_event({
                    "type": "model_activity",
                    "phase": "done",
                    "status": status,
                    "tool": tool_name,
                    "text": summary,
                })
            )

    def _on_speech_created(ev: Any):
        handle = getattr(ev, "speech_handle", None)
        if not handle:
            return

        def _on_item_added(item: Any):
            nonlocal last_agent_transcript
            role = str(getattr(item, "role", "")).lower()
            text = _extract_item_text(item)
            if ("assistant" in role or "model" in role) and text and text != last_agent_transcript:
                asyncio.create_task(
                    publish_session_event({
                        "type": "agent_transcript",
                        "text": text,
                        "final": False,
                    })
                )

        if hasattr(handle, "_add_item_added_callback"):
            handle._add_item_added_callback(_on_item_added)

    session.on("user_input_transcribed", _on_user_input_transcribed)
    session.on("conversation_item_added", _on_conversation_item_added)
    session.on("tool_execution_updated", _on_tool_execution_updated)
    session.on("speech_created", _on_speech_created)

    def _on_data_received(data: rtc.DataPacket):
        """Handle explicit interrupt signals and typed commands from the client."""
        try:
            msg = json.loads(data.data.decode("utf-8"))
            if not isinstance(msg, dict):
                return

            if msg.get("type") == "interrupt":
                session.interrupt(force=True)
                logger.info("Voice session interrupted by user.")
            elif msg.get("type") == "user_text":
                text = msg.get("text", "").strip()
                if text:
                    logger.info("Received typed command over data channel: %s", text)
                    asyncio.create_task(
                        publish_session_event({
                            "type": "user_transcript",
                            "text": text,
                            "final": True,
                        })
                    )
                    asyncio.create_task(
                        publish_session_event({
                            "type": "model_activity",
                            "phase": "start",
                            "tool": "orchestrator",
                            "text": f"Processing: '{text}'",
                        })
                    )

                    async def _handle_typed_command(cmd_text: str):
                        try:
                            from app.workers.orchestrator import orchestrator
                            res = await orchestrator.dispatch(cmd_text)
                            reply = res.get("message", "Action completed.")
                            act = res.get("action", "orchestrator")
                            await publish_session_event({
                                "type": "model_activity",
                                "phase": "done",
                                "status": "done",
                                "tool": act,
                                "text": reply,
                            })
                            await publish_session_event({
                                "type": "agent_transcript",
                                "text": reply,
                                "final": True,
                            })
                        except Exception as err:
                            logger.error("Error executing typed command: %s", err)
                            await publish_session_event({
                                "type": "model_activity",
                                "phase": "done",
                                "status": "error",
                                "tool": "orchestrator",
                                "text": f"Error: {err}",
                            })
                            await publish_session_event({
                                "type": "agent_transcript",
                                "text": f"Sorry, I encountered an error: {err}",
                                "final": True,
                            })

                    asyncio.create_task(_handle_typed_command(text))
        except Exception as exc:
            logger.debug("Error handling data packet: %s", exc)

    ctx.room.on("data_received", _on_data_received)

    @ctx.room.on("disconnected")
    def _on_room_disconnected():
        global _active_session
        if _active_session is session:
            _active_session = None
        if get_active_room() is ctx.room:
            set_active_room(None)
        logger.info("Voice agent session disconnected for room: %s", getattr(ctx.room, "name", "unknown"))

    await session.start(agent=agent, room=ctx.room)


def run_agent():
    """Run the voice agent server."""
    cli.run_app(server)


if __name__ == "__main__":
    run_agent()
