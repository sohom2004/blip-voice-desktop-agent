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
from app.tools.desktop.screen_capture import screen_capture
from app.tools.desktop.ui_automation import UIElement, ui_inspector

logger = logging.getLogger(__name__)


class VisionGroundingEngine:
    """Manages visual grounding, element tagging, and coordinate mapping."""

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


# Global vision grounding singleton
vision_engine = VisionGroundingEngine()
