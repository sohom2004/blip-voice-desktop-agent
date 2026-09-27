"""Complex Reasoning and Multimodal Vision LLM Worker (System Two).

Powered by Gemini (gemini-3.6-flash) via the Google GenAI SDK.
Handles open-ended planning, multi-step execution, and screenshot-based visual grounding
when escalated by Jev System One.
"""

import asyncio
import logging
from typing import Any

from google import genai
from google.genai import types

from app.config import settings
from app.tools.desktop.mouse_keyboard import mouse_keyboard
from app.tools.desktop.ui_automation import ui_inspector
from app.tools.desktop.window_manager import window_manager
from app.tools.system.file_ops import file_manager
from app.tools.system.terminal import terminal_manager
from app.workers.vision import vision_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool implementations provided to Gemini
# ---------------------------------------------------------------------------

def focus_window(query: str) -> str:
    """Bring an application window to the foreground by title, process name, or HWND."""
    success = window_manager.bring_to_front(query)
    if success:
        return f"Successfully focused window matching '{query}'."
    return f"Failed to find or focus window matching '{query}'."


def list_open_windows() -> str:
    """List all currently open, visible top-level application windows on the desktop."""
    windows = window_manager.list_windows()
    if not windows:
        return "No visible windows found."
    lines = [f"- [{w.hwnd}] {w.process_name}: '{w.title}' (active={w.is_active})" for w in windows]
    return "Open Windows:\n" + "\n".join(lines)


def click_mouse(x: int, y: int, button: str = "left") -> str:
    """Click at screen coordinates (x, y) with specified mouse button ('left' or 'right')."""
    cx, cy = mouse_keyboard.click(x=x, y=y, button=button)  # type: ignore
    return f"Clicked mouse at ({cx}, {cy}) with button '{button}'."


def type_text(text: str) -> str:
    """Type text into the currently active or focused input control."""
    mouse_keyboard.type_text(text)
    return f"Typed {len(text)} characters into active control."


def press_hotkey(keys: str) -> str:
    """Press a key combination, e.g. 'ctrl+s', 'ctrl+c', 'ctrl+v', 'alt+tab', 'enter', 'esc'."""
    key_list = [k.strip().lower() for k in keys.split("+")]
    mouse_keyboard.hotkey(*key_list)
    return f"Pressed hotkey combo: {'+'.join(key_list)}"


def scroll(direction: str = "down", amount: int = 5) -> str:
    """Scroll mouse wheel ('up' or 'down')."""
    dir_clean = "down" if "down" in direction.lower() else "up"
    mouse_keyboard.scroll(clicks=amount, direction=dir_clean)  # type: ignore
    return f"Scrolled {dir_clean} by {amount} clicks."


def execute_terminal_command(command: str) -> str:
    """Execute a PowerShell command in the terminal and return its stdout and stderr."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(terminal_manager.execute(command)))
            res = future.result(timeout=35.0)
    else:
        res = loop.run_until_complete(terminal_manager.execute(command))

    output = res.output
    return f"Exit code {res.exit_code} (took {res.duration_seconds}s):\n{output[:1500]}"


def read_file(path: str, start_line: int = 1, end_line: int = 100) -> str:
    """Read a slice of lines from a file."""
    res = file_manager.read_file(path, start_line=start_line, end_line=end_line)
    if res.get("success"):
        return f"File: {path} (lines {res['start_line']}-{res['end_line']} of {res['total_lines']}):\n{res['content']}"
    return f"Error reading file: {res.get('error')}"


def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with given text content."""
    res = file_manager.write_file(path, content, overwrite=True)
    if res.get("success"):
        return f"Successfully wrote {res['bytes_written']} bytes to {path}."
    return f"Error writing file: {res.get('error')}"


def patch_file(path: str, target: str, replacement: str) -> str:
    """Replace an exact substring within a file with replacement text."""
    res = file_manager.patch_file(path, target, replacement)
    if res.get("success"):
        return f"Successfully patched file {path}."
    return f"Patch failed: {res.get('error')}"


def inspect_screen() -> str:
    """Inspect current screen, active window, and interactive UI controls with their pixel coordinates."""
    active_hwnd, active_title, elements = ui_inspector.inspect_active_window(max_elements=30)
    lines = [f"Active Window: '{active_title}' (HWND: {active_hwnd})", "Interactive Elements:"]
    for el in elements:
        clean_name = el.name.encode("ascii", errors="replace").decode("ascii").replace("?", "")
        lines.append(f" - [{el.id}] {el.control_type}: '{clean_name or el.control_type}' at center {el.center}")
    return "\n".join(lines)


def open_url_or_search(query_or_url: str) -> str:
    """Open a website URL or perform a web search in the user's default browser."""
    import webbrowser
    query_clean = query_or_url.strip()
    if query_clean.startswith("http://") or query_clean.startswith("https://"):
        webbrowser.open(query_clean)
        return f"Opened web URL: {query_clean}"
    elif "." in query_clean and " " not in query_clean:
        url = "https://" + query_clean
        webbrowser.open(url)
        return f"Opened web URL: {url}"
    else:
        url = f"https://www.google.com/search?q={query_clean.replace(' ', '+')}"
        webbrowser.open(url)
        return f"Searched the web for: '{query_clean}'"


def open_application(app_name: str) -> str:
    """Launch any application or tool on this PC (e.g. 'calc', 'chrome', 'notepad', 'code', 'spotify', 'terminal')."""
    success = window_manager.launch_app(app_name)
    if success:
        return f"Launched application: {app_name}"
    return f"Failed to launch application: {app_name}"


# ---------------------------------------------------------------------------
# Complex LLM Worker Class
# ---------------------------------------------------------------------------

class ComplexLLMWorker:
    """System Two Worker managing multi-step reasoning and multimodal vision tasks."""

    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = settings.complex_llm_model
        self.tool_list = [
            open_url_or_search,
            open_application,
            focus_window,
            list_open_windows,
            click_mouse,
            type_text,
            press_hotkey,
            scroll,
            execute_terminal_command,
            read_file,
            write_file,
            patch_file,
            inspect_screen,
        ]
        self.tool_map = {fn.__name__: fn for fn in self.tool_list}

    def execute_task(
        self,
        task_instruction: str,
        initial_state: dict[str, Any] | None = None,
        include_screenshot: bool = True,
        max_steps: int = 8,
    ) -> dict[str, Any]:
        """Execute complex multi-step task with tools and multimodal vision."""
        logger.info("ComplexLLMWorker started for task: %s", task_instruction)

        # 1. Capture annotated screenshot if requested
        image_bytes: bytes | None = None
        tags_summary: str = ""
        if include_screenshot:
            try:
                annotated_img, tags = vision_engine.get_annotated_screenshot(max_marks=25)
                image_bytes = vision_engine.image_to_bytes(annotated_img, max_dim=1280)
                tag_lines = [f"Tag {t['tag']}: {t['type']} '{t['name']}' center={t['center']}" for t in tags[:15]]
                tags_summary = "\nVisible Screen Tags:\n" + "\n".join(tag_lines)
            except Exception as exc:
                logger.warning("Could not capture screenshot for worker: %s", exc)

        # 2. System prompt
        system_instruction = (
            "You are the System Two desktop automation agent controlling a Windows PC.\n"
            "You have complete control over the desktop via tools: window management, mouse clicks at coordinates, "
            "keyboard typing, hotkeys, terminal commands, and file operations.\n\n"
            "Guidelines:\n"
            "1. Analyze the user task and current screen state.\n"
            "2. When interacting with UI, use 'click_mouse' with the exact center coordinates of the target element, "
            "   or 'type_text' to enter data.\n"
            "3. If an application window needs to be brought up first, call 'focus_window'.\n"
            "4. For coding, file inspection, or command line tasks, use 'execute_terminal_command', 'write_file', or 'read_file'.\n"
            "5. Execute necessary actions step-by-step using your tools, then provide a concise summary of what was accomplished."
        )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=self.tool_list,
            temperature=0.1,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # Build initial prompt message
        user_prompt = f"Goal: {task_instruction}\n"
        if initial_state:
            active_win = initial_state.get("active_window", {})
            user_prompt += f"Active Window: {active_win.get('title')} ({active_win.get('process')})\n"
        if tags_summary:
            user_prompt += tags_summary + "\n"

        user_parts: list[types.Part] = [types.Part.from_text(text=user_prompt)]
        if image_bytes:
            user_parts.append(
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            )

        conversation: list[types.Content] = [
            types.Content(role="user", parts=user_parts)
        ]

        try:
            for step in range(max_steps):
                resp = None
                raw_models = [self.model_name, "gemini-3.6-flash", "gemini-2.5-flash"]
                fallback_models = []
                for m in raw_models:
                    if m and m not in fallback_models:
                        fallback_models.append(m)
                last_err = None

                for model_candidate in fallback_models:
                    try:
                        resp = self.client.models.generate_content(
                            model=model_candidate,
                            contents=conversation,
                            config=config,
                        )
                        break
                    except Exception as exc:
                        last_err = exc
                        if "429" in str(exc) or "503" in str(exc):
                            logger.warning("Model %s hit quota/demand (429/503). Failing over...", model_candidate)
                            continue
                        raise

                if resp is None:
                    if last_err:
                        raise last_err
                    raise RuntimeError("No available Gemini models could fulfill the request.")

                if resp.function_calls:
                    # Append model's candidate turn
                    if resp.candidates and resp.candidates[0].content:
                        conversation.append(resp.candidates[0].content)

                    # Execute tool calls
                    response_parts: list[types.Part] = []
                    for fc in resp.function_calls:
                        fn_name = fc.name
                        fn_args = fc.args or {}
                        logger.info("Executing tool: %s with args %s", fn_name, fn_args)
                        fn = self.tool_map.get(fn_name)
                        if fn:
                            try:
                                fn_res = fn(**fn_args)
                            except Exception as exc:
                                fn_res = f"Execution error in {fn_name}: {exc}"
                        else:
                            fn_res = f"Unknown tool: {fn_name}"

                        response_parts.append(
                            types.Part.from_function_response(
                                name=fn_name,
                                response={"result": str(fn_res)},
                            )
                        )

                    conversation.append(types.Content(role="user", parts=response_parts))
                else:
                    # Final text response reached
                    final_text = resp.text or "Task execution finished."
                    return {
                        "status": "success",
                        "summary": final_text.strip(),
                        "steps": step + 1,
                    }

            return {
                "status": "partial",
                "summary": "Completed maximum execution steps.",
                "steps": max_steps,
            }

        except Exception as exc:
            logger.error("LLM Worker execution error: %s", exc)
            return {
                "status": "error",
                "summary": f"Worker encountered an error: {exc}",
            }


# Global complex worker singleton
llm_worker = ComplexLLMWorker()
