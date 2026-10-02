"""Autonomous Chain-of-Thought (CoT) Reasoning Worker & Task Harness.

Powered by NVIDIA Nemotron 3.5 Lightning via OpenRouter.
Implements:
1. Zero-Clarification Policy: Never asks user for discoverable items (paths, folders, window IDs).
2. Autonomous Path & Entity Discovery: Self-locates directories and workspaces.
3. Multi-Instance Window Indexing & Disambiguation: Directly addresses 'terminal 1', 'terminal 2', 'explorer 1'.
4. Solve-Verify Loop (Pi Agent / Hermes Agent harness pattern).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from openai import OpenAI

from app.config import settings
from app.tools.desktop.window_manager import window_manager
from app.tools.system.developer_ops import dev_ops
from app.tools.system.file_ops import file_manager
from app.tools.system.memory import memory_store
from app.tools.system.terminal import terminal_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool Implementations for CoT Worker
# ---------------------------------------------------------------------------

def cot_find_directory(query: str) -> str:
    """Search disk for a folder or project path matching a query name without asking the user."""
    res = file_manager.find_directory(query)
    if res.get("success"):
        return f"Found directory: {res.get('best_match')} (name: {res.get('best_match_name')}, score: {res.get('score')})"
    return f"Directory not found for query '{query}': {res.get('error')}"


def cot_open_terminal_in_directory(path: str, command: str = "") -> str:
    """Launch a new Windows Terminal window in the specified directory path and optionally run a command (e.g. 'agy')."""
    res = terminal_manager.open_terminal_window(path, command=command if command else None)
    if res.get("success"):
        return f"Successfully opened terminal in '{res.get('directory')}' (window: {res.get('window')}, hwnd: {res.get('hwnd')})."
    return f"Failed to open terminal in '{path}': {res.get('error')}"


def cot_focus_window(query: str) -> str:
    """Bring an application window to the foreground by alias ('terminal 1', 'terminal 2', 'explorer 1'), title, or process."""
    win = window_manager.find_window(query)
    if not win:
        return f"Window '{query}' not found."
    success = window_manager.bring_to_front(win.hwnd)
    if success:
        return f"Focused window: [{win.alias}] '{win.title}' (HWND {win.hwnd})."
    return f"Could not bring window '{query}' to front."


def cot_write_to_window(query: str, text: str, press_enter: bool = True) -> str:
    """Focus a specific numbered window (e.g. 'terminal 1', 'terminal 2', 'explorer 1') and type text or commands into it."""
    res = window_manager.send_input_to_window(query, text=text, press_enter=press_enter)
    if res.get("success"):
        return f"Typed '{text}' into [{res.get('window')}]."
    return f"Failed to write to window '{query}': {res.get('error')}"


def cot_list_open_windows() -> str:
    """List all currently open desktop windows with their numbered canonical aliases."""
    windows = window_manager.list_windows()
    if not windows:
        return "No open windows found."
    lines = [
        f"- [{w.alias}] {w.process_name} (HWND: {w.hwnd}, Active: {w.is_active}): '{w.title}'"
        for w in windows
    ]
    return "Open Windows:\n" + "\n".join(lines)


def cot_open_application(app_name: str) -> str:
    """Launch any desktop application (calc, notepad, chrome, code, spotify, etc.)."""
    ok = window_manager.launch_app(app_name)
    if ok:
        return f"Launched application '{app_name}'."
    return f"Failed to launch application '{app_name}'."


def cot_execute_terminal_command(command: str) -> str:
    """Execute a PowerShell command in the background and return its stdout and stderr."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            res = pool.submit(asyncio.run, terminal_manager.execute(command, timeout=20.0)).result()
    else:
        res = loop.run_until_complete(terminal_manager.execute(command, timeout=20.0))

    return res.output or f"Command finished with exit code {res.exit_code}."


def cot_remember_fact(key: str, value: str, category: str = "general") -> str:
    """Store or update a user fact, preference, or project shortcut in persistent memory."""
    return memory_store.remember(key, value, category=category)


def cot_recall_facts(query: str = "") -> str:
    """Recall stored memories or facts by keyword or category."""
    results = memory_store.recall(query)
    if not results:
        return f"No memories found matching '{query}'."
    lines = [f"- [{m.get('category')}] {m.get('key')}: {m.get('value')}" for m in results]
    return "Stored Memories:\n" + "\n".join(lines)


def cot_get_directory_tree(dir_path: str = ".", max_depth: int = 2) -> str:
    """Render a visual ASCII directory tree of a project or folder."""
    return dev_ops.get_directory_tree(dir_path=dir_path, max_depth=max_depth)


def cot_grep_code(query: str, dir_path: str = ".", file_pattern: str = "*.*") -> str:
    """Search for regex or text across codebase files."""
    return dev_ops.grep_code(query=query, dir_path=dir_path, file_pattern=file_pattern)


def cot_get_git_status(repo_path: str = ".") -> str:
    """Inspect Git branch, changed files, and latest commit."""
    return dev_ops.get_git_status(repo_path=repo_path)


def cot_execute_python_code(code: str) -> str:
    """Execute a Python snippet and return stdout/stderr."""
    return dev_ops.execute_python_code(code=code)


def cot_find_process_by_port(port: int) -> str:
    """Find which process is listening on a given network port."""
    res = dev_ops.find_process_by_port(port=port)
    if res.get("success"):
        procs = ", ".join([f"{p['process_name']} (PID {p['pid']})" for p in res.get("processes", [])])
        return f"Port {port} is used by: {procs}"
    return res.get("message") or res.get("error", "Not found.")


def cot_kill_process(name_or_pid: str) -> str:
    """Terminate a running process by name (e.g. 'node', 'python') or PID."""
    res = dev_ops.kill_process(name_or_pid)
    if res.get("success"):
        return f"Successfully killed: {res.get('killed')}"
    return f"Failed to kill process '{name_or_pid}': {res.get('error')}"


# ---------------------------------------------------------------------------
# Tool Schema Definitions for OpenRouter / Nemotron
# ---------------------------------------------------------------------------

COT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_directory",
            "description": "Autonomously search disk for a folder, project, or workspace path matching a query without asking the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Folder or project name (e.g. 'voice agent', 'dental', 'automations')"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_terminal_in_directory",
            "description": "Open a new Windows Terminal window in the designated folder path and optionally run a startup command (e.g. 'agy', 'npm start').",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute folder path to open the terminal in."},
                    "command": {"type": "string", "description": "Optional command to execute automatically in the terminal upon launch."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Bring an open window to the foreground using its numbered alias ('terminal 1', 'terminal 2', 'explorer 1', 'browser 1') or title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Window alias ('terminal 1', 'terminal 2', 'explorer 1') or title substring."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_to_window",
            "description": "Focus a specific numbered window (e.g. 'terminal 1', 'terminal 2') and type commands or text into it, then press Enter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Target window alias (e.g. 'terminal 1', 'terminal 2', 'explorer 1')."},
                    "text": {"type": "string", "description": "Text or command to type into the window."},
                    "press_enter": {"type": "boolean", "description": "Whether to press Enter after typing. Defaults to true."}
                },
                "required": ["query", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_open_windows",
            "description": "List all currently open windows with their canonical numbered aliases ('terminal 1', 'terminal 2', 'explorer 1', etc.).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Launch an application (e.g. 'calc', 'notepad', 'chrome', 'code', 'explorer', 'terminal').",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Application name to launch"}
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_terminal_command",
            "description": "Run a PowerShell command in the background and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "PowerShell command to execute"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Store a user preference, folder shortcut, or key fact into persistent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key identifier for the memory"},
                    "value": {"type": "string", "description": "Information or path to remember"},
                    "category": {"type": "string", "description": "Category (e.g. 'preference', 'project', 'shortcut')"}
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_facts",
            "description": "Recall stored memories, user preferences, or project shortcuts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword or empty to list all memories"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_directory_tree",
            "description": "Render a clean visual ASCII directory tree of a project or folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {"type": "string", "description": "Directory path to inspect (defaults to '.')"},
                    "max_depth": {"type": "integer", "description": "Maximum tree depth (default 2)"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_code",
            "description": "Fast regex search across files in a codebase returning matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regex or text string to search"},
                    "dir_path": {"type": "string", "description": "Directory to search in (defaults to '.')"},
                    "file_pattern": {"type": "string", "description": "File glob pattern like '*.py' or '*.*'"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_git_status",
            "description": "Inspect Git repository branch, changed files, and latest commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Path to Git repository"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python_code",
            "description": "Run Python code in a safe subprocess and return printed output/result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_process_by_port",
            "description": "Find which process is listening on a network port (e.g. 8000, 3000).",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "description": "Port number to check"}
                },
                "required": ["port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": "Terminate a running process by name or PID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_pid": {"type": "string", "description": "Process name (e.g. 'node', 'python') or PID"}
                },
                "required": ["name_or_pid"],
            },
        },
    },
]

COT_TOOL_MAP: dict[str, Callable[..., Any]] = {
    "find_directory": cot_find_directory,
    "open_terminal_in_directory": cot_open_terminal_in_directory,
    "focus_window": cot_focus_window,
    "write_to_window": cot_write_to_window,
    "list_open_windows": cot_list_open_windows,
    "open_application": cot_open_application,
    "execute_terminal_command": cot_execute_terminal_command,
    "remember_fact": cot_remember_fact,
    "recall_facts": cot_recall_facts,
    "get_directory_tree": cot_get_directory_tree,
    "grep_code": cot_grep_code,
    "get_git_status": cot_get_git_status,
    "execute_python_code": cot_execute_python_code,
    "find_process_by_port": cot_find_process_by_port,
    "kill_process": cot_kill_process,
}


SYSTEM_PROMPT = """You are the Autonomous Chain-of-Thought (CoT) Reasoning Worker for a Windows 11 Voice Desktop Assistant.
Operating Environment: Windows 11 PC.
Current User: Sohom.

STRICT AUTONOMOUS REASONING CONTRACT:
1. NEVER ASK QUESTIONS: You MUST NEVER ask the user clarifying questions like "where should I open it?", "what is the path?", or "I can't figure this out". You have full autonomous tools to discover any missing information yourself.
2. MISSING PATH RESOLUTION: If the user says "open antigravity terminal in <project/folder>", immediately call `find_directory(query='<project>')`. Once the directory is resolved, call `open_terminal_in_directory(path=..., command='agy')`.
3. NUMBERED WINDOW DISAMBIGUATION: When multiple instances of the same application are open, they are canonically indexed:
   - Terminals: 'terminal 1', 'terminal 2', 'terminal 3'
   - Explorers: 'explorer 1', 'explorer 2'
   - Browsers: 'browser 1', 'browser 2'
   - Code/Editors: 'code 1', 'editor 1'
   If the user says "terminal 1", "terminal 2", "write X into terminal 3", directly use `focus_window` or `write_to_window` targeting that exact alias.
4. CHAIN-OF-THOUGHT EXECUTION:
   - Analyze user intent.
   - Self-resolve missing entities using discovery tools.
   - Execute target actions sequentially.
   - Verify outcome and return a concise, definitive confirmation of what was completed.
"""


class CoTReasoningWorker:
    """Autonomous CoT Reasoning Worker powered by Nemotron 3.5 Lightning."""

    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url
        self.model_name = settings.cot_reasoning_model
        self.client: OpenAI | None = None
        self._init_client()

    def _init_client(self):
        if self.api_key:
            try:
                self.client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
                logger.info("CoTReasoningWorker initialized with model: %s", self.model_name)
            except Exception as exc:
                logger.error("Failed to initialize CoTReasoningWorker OpenAI client: %s", exc)

    def execute_task(
        self,
        user_instruction: str,
        max_turns: int = 6,
    ) -> dict[str, Any]:
        """Execute task using the autonomous CoT reasoning loop."""
        if not self.client:
            self._init_client()
        if not self.client:
            return {"status": "error", "summary": "OpenRouter API client not configured."}

        start_time = time.perf_counter()
        logger.info("CoTReasoningWorker executing: '%s'", user_instruction)

        # Inject current window state context into initial prompt
        windows = window_manager.list_windows()
        win_preview = ", ".join([f"[{w.alias}] '{w.title}'" for w in windows[:8]]) if windows else "None"

        user_content = (
            f"User Instruction: {user_instruction}\n"
            f"Current Open Windows: {win_preview}\n"
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        steps_log: list[dict[str, Any]] = []

        try:
            for turn in range(max_turns):
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=COT_TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                )

                choice = response.choices[0]
                message = choice.message
                messages.append(message)

                # Check for tool calls
                if message.tool_calls:
                    for tc in message.tool_calls:
                        fn_name = tc.function.name
                        try:
                            fn_args = json.loads(tc.function.arguments or "{}")
                        except Exception:
                            fn_args = {}

                        logger.info("CoT Worker Tool Call: %s (args: %s)", fn_name, fn_args)
                        fn = COT_TOOL_MAP.get(fn_name)
                        if fn:
                            try:
                                tool_result = fn(**fn_args)
                            except Exception as exc:
                                tool_result = f"Error executing {fn_name}: {exc}"
                        else:
                            tool_result = f"Unknown tool: {fn_name}"

                        steps_log.append({
                            "turn": turn + 1,
                            "tool": fn_name,
                            "args": fn_args,
                            "result": str(tool_result),
                        })

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": fn_name,
                            "content": str(tool_result),
                        })
                else:
                    # Final response reached
                    final_summary = message.content or "Task completed successfully."
                    elapsed = round(time.perf_counter() - start_time, 2)
                    return {
                        "status": "success",
                        "summary": final_summary.strip(),
                        "steps": len(steps_log),
                        "duration_seconds": elapsed,
                        "trace": steps_log,
                    }

            elapsed = round(time.perf_counter() - start_time, 2)
            return {
                "status": "partial",
                "summary": "Completed maximum reasoning turns.",
                "steps": len(steps_log),
                "duration_seconds": elapsed,
                "trace": steps_log,
            }

        except Exception as exc:
            logger.error("CoTReasoningWorker error: %s", exc)
            return {
                "status": "error",
                "summary": f"CoT reasoning worker encountered error: {exc}",
                "trace": steps_log,
            }


# Global singleton instance
cot_worker = CoTReasoningWorker()
