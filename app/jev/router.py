"""Jev System One Reflex Router and Fast-Path Executor.

Evaluates user commands against live desktop state using TypeSafe primitives:
- Choice: Dispatches action types, candidate windows, and UI controls
- Noul: Detects when escalation to the System Two LLM worker is required
- Score: Evaluates execution confidence

Executes direct desktop/window/control actions immediately (~150-250ms)
or triggers escalation for complex reasoning tasks.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from app.jev.client import jev_client
from app.jev.primitives import make_choice_question, make_noul_question, make_score_question
from app.jev.state_builder import state_builder
from app.tools.desktop.mouse_keyboard import mouse_keyboard
from app.tools.desktop.window_manager import window_manager
from app.tools.system.terminal import terminal_manager

logger = logging.getLogger(__name__)


import re

def extract_web_target(cmd: str) -> str:
    c = cmd.strip()
    c_lower = c.lower()
    shortcuts = {
        "youtube": "https://www.youtube.com",
        "github": "https://www.github.com",
        "google": "https://www.google.com",
        "reddit": "https://www.reddit.com",
        "twitter": "https://www.x.com",
        "x": "https://www.x.com",
    }
    m = re.search(r"(?:search\s+(?:for|google\s+for|on\s+google\s+for)?\s*)(.+)", c_lower)
    if m:
        term = m.group(1).strip()
        if term in shortcuts:
            return shortcuts[term]
        return term

    m2 = re.search(r"(?:open|go\s+to|navigate\s+to)\s+(?:the\s+browser\s+and\s+)?([a-zA-Z0-9_\-\.]+)", c_lower)
    if m2:
        site = m2.group(1).strip()
        if site in shortcuts:
            return shortcuts[site]
        if "." in site:
            return site
    return c


@dataclass
class ReflexDecision:
    action_type: str
    target_window_id: str | None
    target_element_id: str | None
    needs_escalation: bool
    escalation_probability: float
    confidence_score: float
    confidence_level: str
    raw_answers: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JevRouter:
    """Reflex controller powered by TypeSafe System One (Jev)."""

    def __init__(self):
        pass

    async def evaluate_command(self, user_command: str) -> tuple[dict[str, Any], ReflexDecision]:
        """Build desktop state and query Jev with parallel typed questions."""
        state = state_builder.build_state(user_command)

        # 1. Action type choice criteria
        action_criteria = {
            "launch_app": "User wants to open or launch an application or tool (e.g. 'open calculator', 'open chrome', 'launch notepad', 'open spotify')",
            "open_web": "User wants to open a website, browser, or search the web (e.g. 'open youtube', 'open the browser and search for youtube', 'search for python', 'open google.com')",
            "focus_window": "User ONLY wants to switch to or bring an ALREADY OPEN window from open_windows to the front (e.g. 'switch to vs code', 'bring notepad to front')",
            "click_element": "User wants to click a visible button, tab, menu, link, or control in the active window",
            "type_text": "User wants to type literal text into the active input field",
            "keyboard_shortcut": "User wants to trigger a hotkey combo (e.g. Ctrl+S, Ctrl+C, Ctrl+V, Alt+Tab, Win+R)",
            "scroll_page": "User wants to scroll up or down on the current screen/page",
            "terminal_command": "User provided a literal terminal command line to execute (e.g. 'git status', 'dir', 'python main.py')",
            "escalate_to_llm": "User wants multi-step coding, deep troubleshooting, reading/editing files, or visual inspection of complex window contents",
            "task_complete": "The user's intent is already satisfied or just small talk",
        }

        # 2. Window candidates choice criteria
        window_criteria: dict[str, str | None] = {"none": "No matching window"}
        for win in state["open_windows"]:
            w_id = win["id"]
            window_criteria[w_id] = f"{win['process']}: {win['title'][:50]}"

        # 3. UI element candidates choice criteria
        element_criteria: dict[str, str | None] = {"none": "No matching UI control"}
        for el in state["interactive_elements"]:
            e_id = el["id"]
            element_criteria[e_id] = f"{el['type']}: '{el['label']}'"

        # Construct parallel questions map
        questions = {
            "action_type": make_choice_question(
                instructions="What primary action should be performed to fulfill the `user_command`?",
                criteria=action_criteria,
            ),
            "target_window": make_choice_question(
                instructions="Which window in `open_windows` should be focused or targeted for the `user_command`?",
                criteria=window_criteria,
            ),
            "target_element": make_choice_question(
                instructions="Which element in `interactive_elements` should be clicked or interacted with?",
                criteria=element_criteria,
            ),
            "needs_escalation": make_noul_question(
                instructions=(
                    "Does `user_command` ask to search the web, read/analyze files, write code, "
                    "or perform multi-step actions (e.g. 'open X and search Y') that require browser navigation or reasoning?"
                ),
                true_criteria="Requires multi-step execution, deep web search/research, reading/analyzing file contents, code synthesis, or vision",
                false_criteria="Can be executed directly by a single app launch, web open, window focus, click, or hotkey",
            ),
            "confidence": make_score_question(
                instructions="How clear and actionable is `user_command` given the current desktop state?",
                levels=[
                    "Unclear, ambiguous, or lacks context",
                    "Partially actionable with slight ambiguity",
                    "Completely clear, direct, and actionable",
                ],
            ),
        }

        # Send to Jev endpoint
        response = await jev_client.evaluate(state=state, questions=questions)
        answers = response.get("answers", {})

        action_ans = answers.get("action_type", {}).get("choice", "escalate_to_llm")
        target_win = answers.get("target_window", {}).get("choice")
        target_el = answers.get("target_element", {}).get("choice")
        escalation_prob = answers.get("needs_escalation", {}).get("noul", 0.0)
        confidence_score = answers.get("confidence", {}).get("score", 1.0)

        # Interpret confidence score
        if confidence_score >= 1.5:
            conf_level = "High"
        elif confidence_score >= 0.7:
            conf_level = "Medium"
        else:
            conf_level = "Low"

        # Detect compound tasks or web queries that require System Two
        cmd_lower = user_command.lower().strip()
        is_direct_web = (
            action_ans == "open_web"
            or bool(re.search(r"(?:open\s+(?:the\s+)?browser\s+and\s+search|search\s+(?:for|google)|open\s+(?:youtube|google|github))", cmd_lower))
        )
        if is_direct_web:
            action_ans = "open_web"

        is_compound = (" and " in cmd_lower) and not is_direct_web
        is_deep_research = any(kw in cmd_lower for kw in ("find ", "look up ", "summarize ", "browse ", "analyze ", "inspect "))

        # Determine escalation threshold
        should_escalate = (
            (action_ans == "escalate_to_llm" and not is_direct_web)
            or is_compound
            or is_deep_research
            or (escalation_prob >= 0.65 and not is_direct_web)
            or (confidence_score < 0.6 and escalation_prob > 0.4 and not is_direct_web)
        )

        decision = ReflexDecision(
            action_type=action_ans,
            target_window_id=target_win if target_win != "none" else None,
            target_element_id=target_el if target_el != "none" else None,
            needs_escalation=should_escalate,
            escalation_probability=escalation_prob,
            confidence_score=confidence_score,
            confidence_level=conf_level,
            raw_answers=answers,
        )

        return state, decision

    async def execute_reflex(
        self,
        user_command: str,
    ) -> dict[str, Any]:
        """Evaluate and immediately execute fast-path action if no escalation needed."""
        state, decision = await self.evaluate_command(user_command)

        if decision.needs_escalation:
            logger.info("Jev reflex routed to System Two (escalation probability: %.2f)", decision.escalation_probability)
            return {
                "status": "escalate",
                "reason": "Task requires complex reasoning, vision, or multi-step execution",
                "state": state,
                "decision": decision.to_dict(),
            }

        # Fast-Path Execution
        action = decision.action_type
        result_message = ""
        success = True

        if action == "launch_app":
            success = window_manager.launch_app(user_command)
            result_message = f"Launched application for command: '{user_command}'"
            state_builder.record_action("launch_app", {"command": user_command})

        elif action == "focus_window":
            target_hwnd = None
            target_title = ""
            if decision.target_window_id:
                for w in state["open_windows"]:
                    if w["id"] == decision.target_window_id:
                        target_hwnd = w["hwnd"]
                        target_title = w["title"]
                        break

            if target_hwnd:
                success = window_manager.bring_to_front(target_hwnd)
                result_message = f"Focused window: '{target_title}'"
                state_builder.record_action("focus_window", {"title": target_title, "hwnd": target_hwnd})
            else:
                # If window was not already open, launch it
                success = window_manager.launch_app(user_command)
                result_message = f"Window was not open; launched application: '{user_command}'"
                state_builder.record_action("launch_app", {"command": user_command})

        elif action == "click_element" and decision.target_element_id:
            # Look up element center coordinates from state
            center = None
            el_label = ""
            for el in state["interactive_elements"]:
                if el["id"] == decision.target_element_id:
                    center = el.get("center")
                    el_label = el.get("label", "")
                    break

            if center:
                cx, cy = center
                mouse_keyboard.click(cx, cy)
                result_message = f"Clicked '{el_label}' at ({cx}, {cy})"
                state_builder.record_action("click_element", {"label": el_label, "coords": [cx, cy]})
            else:
                success = False
                result_message = f"Could not find coordinates for element {decision.target_element_id}"

        elif action == "open_web":
            from app.tools.desktop.web_browser import open_url_or_search
            target = extract_web_target(user_command)
            result_message = open_url_or_search(target)
            state_builder.record_action("open_web", {"target": target})

        elif action == "scroll_page":
            direction = "down" if "down" in user_command.lower() else "up"
            mouse_keyboard.scroll(clicks=5, direction=direction)
            result_message = f"Scrolled page {direction}"
            state_builder.record_action("scroll", {"direction": direction})

        elif action == "keyboard_shortcut":
            # Basic common hotkeys
            cmd_lower = user_command.lower()
            if "save" in cmd_lower:
                mouse_keyboard.hotkey("ctrl", "s")
                result_message = "Pressed Ctrl+S (Save)"
            elif "copy" in cmd_lower:
                mouse_keyboard.hotkey("ctrl", "c")
                result_message = "Pressed Ctrl+C (Copy)"
            elif "paste" in cmd_lower:
                mouse_keyboard.hotkey("ctrl", "v")
                result_message = "Pressed Ctrl+V (Paste)"
            elif "new tab" in cmd_lower:
                mouse_keyboard.hotkey("ctrl", "t")
                result_message = "Pressed Ctrl+T (New Tab)"
            elif "close" in cmd_lower and "tab" in cmd_lower:
                mouse_keyboard.hotkey("ctrl", "w")
                result_message = "Pressed Ctrl+W (Close Tab)"
            else:
                result_message = "Executed keyboard shortcut"

        elif action == "terminal_command":
            # Strip preamble if any and execute in powershell
            cmd = user_command.strip()
            res = await terminal_manager.execute(cmd)
            success = (res.exit_code == 0)
            result_message = f"Terminal exit code {res.exit_code}: {res.stdout.strip()[:200]}"

        elif action == "task_complete":
            result_message = "No desktop action required."

        else:
            result_message = f"Executed {action}"

        return {
            "status": "success" if success else "failed",
            "action": action,
            "message": result_message,
            "decision": decision.to_dict(),
        }


# Global Jev router singleton
jev_router = JevRouter()
