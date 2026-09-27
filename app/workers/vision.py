"""Vision Grounding and Screenshot Annotation Tool.

Provides Set-of-Marks (SoM) labeling and coordinate normalization for
multimodal vision LLMs.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types

from app.config import settings
from app.tools.browser.browser_manager import browser_manager
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.ui_automation import UIElement, ui_inspector

logger = logging.getLogger(__name__)

TERMINAL_PROCESSES = {
    "windowsterminal.exe",
    "powershell.exe",
    "cmd.exe",
    "pwsh.exe",
    "conhost.exe",
    "code.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    "mintty.exe",
    "kitty.exe",
}

BROWSER_PROCESSES = {
    "chrome.exe",
    "brave.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
}



class VisionGroundingEngine:
    """Manages visual grounding, element tagging, and coordinate mapping."""

    TERMINAL_PROCESSES = TERMINAL_PROCESSES
    BROWSER_PROCESSES = BROWSER_PROCESSES

    def __init__(self):
        pass

    def get_annotated_screenshot(
        self,
        elements: list[UIElement] | None = None,
        max_marks: int = 35,
    ) -> tuple[Image.Image, list[dict[str, Any]]]:
        """Capture screenshot and draw Set-of-Marks (SoM) bounding tags over elements."""
        img = screen_capture.capture_primary_monitor().convert("RGBA")
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        # If elements not provided, inspect active window
        if elements is None:
            _, _, elements = ui_inspector.inspect_active_window(max_elements=max_marks)

        labeled_elements: list[dict[str, Any]] = []

        # Color palette for tags
        colors = [
            (255, 60, 60, 180),   # Red
            (60, 130, 255, 180),  # Blue
            (40, 190, 70, 180),   # Green
            (240, 160, 20, 180),  # Orange
            (170, 60, 240, 180),  # Purple
        ]

        # Use default font or fallback
        font = ImageFont.load_default()

        tag_idx = 1
        for el in elements[:max_marks]:
            left, top, right, bottom = el.rect
            w = right - left
            h = bottom - top

            if w <= 4 or h <= 4:
                continue

            color = colors[(tag_idx - 1) % len(colors)]
            border_color = (color[0], color[1], color[2], 240)

            # Draw bounding box
            draw.rectangle([left, top, right, bottom], outline=border_color, width=2)

            # Draw tag badge in corner
            badge_text = str(tag_idx)
            badge_w = 18 + (len(badge_text) * 4)
            badge_h = 16
            draw.rectangle([left, max(0, top - badge_h), left + badge_w, top], fill=border_color)
            draw.text((left + 3, max(0, top - badge_h) + 1), badge_text, fill=(255, 255, 255, 255), font=font)

            safe_name = el.name.encode("ascii", errors="replace").decode("ascii").replace("?", "")
            labeled_elements.append(
                {
                    "tag": tag_idx,
                    "id": el.id,
                    "name": safe_name or el.control_type,
                    "type": el.control_type,
                    "center": el.center,
                    "rect": el.rect,
                }
            )
            tag_idx += 1

        # Composite overlay with base screenshot
        annotated = Image.alpha_composite(img, overlay).convert("RGB")
        return annotated, labeled_elements

    def image_to_base64_data_url(self, image: Image.Image, max_dim: int = 1280, quality: int = 80) -> str:
        """Convert a PIL image to base64 JPEG data URL scaled for vision models."""
        img_copy = image.copy()
        if max(img_copy.width, img_copy.height) > max_dim:
            scale = max_dim / float(max(img_copy.width, img_copy.height))
            new_w = int(img_copy.width * scale)
            new_h = int(img_copy.height * scale)
            img_copy = img_copy.resize((new_w, new_h), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img_copy.save(buffer, format="JPEG", quality=quality)
        b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"

    def image_to_bytes(self, image: Image.Image, max_dim: int = 1280, quality: int = 80) -> bytes:
        """Convert a PIL image to JPEG bytes."""
        img_copy = image.copy()
        if max(img_copy.width, img_copy.height) > max_dim:
            scale = max_dim / float(max(img_copy.width, img_copy.height))
            new_w = int(img_copy.width * scale)
            new_h = int(img_copy.height * scale)
            img_copy = img_copy.resize((new_w, new_h), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img_copy.save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()

    async def extract_visual_content(
        self,
        image: Image.Image,
        window_type: str = "general",
        focus_hint: str = "",
    ) -> str:
        """Use fast Gemini multimodal vision to extract dynamic text from terminal, browser, or screen."""
        if not settings.is_gemini_configured():
            return "Gemini API key not configured for visual content extraction."

        try:
            client = genai.Client(api_key=settings.gemini_api_key or settings.google_api_key)
            img_bytes = self.image_to_bytes(image, max_dim=1280)

            if window_type == "terminal":
                prompt = (
                    "This is an image of a terminal/console window. Extract the exact visible text, "
                    "including command prompt, current directory, recent commands, error messages, "
                    "and outputs. Format concisely as plain text without commentary."
                )
            elif window_type == "browser":
                prompt = (
                    "This is an image of a web browser. Extract the visible page content: "
                    "page title/URL if visible, main headings, paragraphs, visible search results or "
                    "form fields, modal dialogs, and active alerts. Be direct and concise."
                )
            else:
                prompt = (
                    "Extract the primary dynamic text, messages, forms, or data displayed "
                    "in the main content area of this application window. Summarize concisely."
                )

            if focus_hint:
                prompt += f"\nSpecifically focus on: {focus_hint}"

            def _call_model():
                models_to_try = ["gemini-2.5-flash", "gemini-3.6-flash"]
                for model_name in models_to_try:
                    try:
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=[
                                prompt,
                                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                            ],
                        )
                        if resp and resp.text:
                            return resp.text.strip()
                    except Exception as exc:
                        logger.warning("Model %s failed for visual content extraction: %s", model_name, exc)
                        continue
                return "Unable to extract visual content from image."

            import asyncio
            return await asyncio.to_thread(_call_model)
        except Exception as exc:
            logger.error("Visual content extraction error: %s", exc)
            return f"Error extracting visual content: {exc}"

    async def inspect_active_content(
        self,
        active_window: Any | None = None,
        screenshot_img: Image.Image | None = None,
        focus_hint: str = "",
    ) -> dict[str, Any]:
        """Inspect dynamic interior content (DOM or visual OCR) of the currently active window."""
        img = screenshot_img or screen_capture.capture_primary_monitor()
        proc = active_window.process_name.lower() if active_window else ""

        is_browser = proc in BROWSER_PROCESSES or "chrome" in proc or "brave" in proc or "edge" in proc
        is_terminal = proc in TERMINAL_PROCESSES or "terminal" in proc or "powershell" in proc or "pwsh" in proc or "cmd" in proc

        if is_browser:
            # Try CDP first
            cdp_data = await browser_manager.extract_page_content()
            if cdp_data and cdp_data.get("text"):
                return {
                    "source": "Browser CDP",
                    "window_type": "browser",
                    "text": f"Page: {cdp_data.get('title')} ({cdp_data.get('url')})\n{cdp_data.get('text')}",
                    "dom_elements": cdp_data.get("elements", []),
                }
            # Fallback to visual multimodal extraction
            vis_text = await self.extract_visual_content(img, window_type="browser", focus_hint=focus_hint)
            return {
                "source": "Browser Multimodal Vision",
                "window_type": "browser",
                "text": vis_text,
                "dom_elements": [],
            }

        if is_terminal:
            vis_text = await self.extract_visual_content(img, window_type="terminal", focus_hint=focus_hint)
            return {
                "source": "Terminal Multimodal Vision",
                "window_type": "terminal",
                "text": vis_text,
                "dom_elements": [],
            }

        # General window or desktop
        vis_text = await self.extract_visual_content(img, window_type="general", focus_hint=focus_hint)
        return {
            "source": "Visual Multimodal Inspection",
            "window_type": "general",
            "text": vis_text,
            "dom_elements": [],
        }



# Global vision grounding singleton
vision_engine = VisionGroundingEngine()
