"""Browser Automation and DOM Interaction Tool.

Provides CDP and Playwright integration for deep browser and Electron DOM control,
inspired by wy-coliney/jev-browser-use:
- Connects to existing Chrome/Edge/Brave instances via CDP or launches automated sessions.
- Extracts clean, indexed interactive DOM elements (buttons, links, form inputs).
- Executes rapid clicks, text typing, navigation, and JS evaluation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)


@dataclass
class DOMElement:
    id: str  # e.g. "dom_1"
    tag: str
    text: str
    role: str
    selector: str
    rect: dict[str, float]
    center: tuple[int, int]
    is_visible: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrowserManager:
    """Manages browser control via Playwright and Chrome DevTools Protocol."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._active_page: Page | None = None
        self._lock = asyncio.Lock()

    async def _ensure_playwright(self):
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def connect_cdp(self, endpoint_url: str = "http://127.0.0.1:9222") -> bool:
        """Connect to an existing browser running with --remote-debugging-port."""
        async with self._lock:
            try:
                await self._ensure_playwright()
                self._browser = await self._playwright.chromium.connect_over_cdp(endpoint_url)
                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    pages = self._context.pages
                    self._active_page = pages[0] if pages else await self._context.new_page()
                else:
                    self._context = await self._browser.new_context()
                    self._active_page = await self._context.new_page()
                logger.info("Connected to browser over CDP at %s", endpoint_url)
                return True
            except Exception as exc:
                logger.warning("Could not connect to CDP at %s: %s", endpoint_url, exc)
                return False

    async def launch_browser(self, headless: bool = False) -> bool:
        """Launch a Chromium browser instance."""
        async with self._lock:
            try:
                await self._ensure_playwright()
                self._browser = await self._playwright.chromium.launch(headless=headless)
                self._context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
                self._active_page = await self._context.new_page()
                logger.info("Launched Chromium browser instance (headless=%s)", headless)
                return True
            except Exception as exc:
                logger.error("Failed to launch browser: %s", exc)
                return False

    async def get_or_create_page(self) -> Page | None:
        """Get the current active page or launch one if none exists."""
        if self._active_page and not self._active_page.is_closed():
            return self._active_page

        # Try connecting via CDP first, then launch if unavailable
        if await self.connect_cdp():
            return self._active_page
        if await self.launch_browser(headless=False):
            return self._active_page
        return None

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate active tab to the specified URL."""
        page = await self.get_or_create_page()
        if not page:
            return {"success": False, "error": "No browser page available"}

        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
            url = "https://" + url

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return {"success": True, "url": page.url, "title": await page.title()}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def extract_interactive_dom(self, max_elements: int = 40) -> list[DOMElement]:
        """Extract interactive DOM elements with CSS selectors and bounding boxes."""
        page = await self.get_or_create_page()
        if not page:
            return []

        js_script = """
        () => {
            const elements = Array.from(document.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"], [role="tab"]'));
            const results = [];
            let idx = 1;
            for (const el of elements) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0') {
                    let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
                    
                    // Generate best unique selector
                    let sel = '';
                    if (el.id) {
                        sel = '#' + el.id;
                    } else if (el.name) {
                        sel = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                    } else if (el.getAttribute('data-testid')) {
                        sel = `[data-testid="${el.getAttribute('data-testid')}"]`;
                    } else {
                        sel = el.tagName.toLowerCase();
                        if (el.className && typeof el.className === 'string') {
                            const firstClass = el.className.split(' ').filter(c => c && !c.includes(':'))[0];
                            if (firstClass) sel += '.' + firstClass;
                        }
                    }

                    results.push({
                        id: 'dom_' + idx++,
                        tag: el.tagName.toLowerCase(),
                        text: text,
                        role: el.getAttribute('role') || el.tagName.toLowerCase(),
                        selector: sel,
                        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height },
                        center: [Math.round(rect.left + rect.width / 2), Math.round(rect.top + rect.height / 2)],
                        is_visible: true
                    });
                }
            }
            return results;
        }
        """
        try:
            raw_elements = await page.evaluate(js_script)
            results = []
            for item in raw_elements[:max_elements]:
                results.append(
                    DOMElement(
                        id=item["id"],
                        tag=item["tag"],
                        text=item["text"],
                        role=item["role"],
                        selector=item["selector"],
                        rect=item["rect"],
                        center=tuple(item["center"]),
                        is_visible=item["is_visible"],
                    )
                )
            return results
        except Exception as exc:
            logger.error("Failed to extract DOM elements: %s", exc)
            return []

    async def click_element(self, selector: str) -> dict[str, Any]:
        """Click element by selector or text."""
        page = await self.get_or_create_page()
        if not page:
            return {"success": False, "error": "No browser page available"}

        try:
            await page.click(selector, timeout=5000)
            return {"success": True, "selector": selector}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def fill_element(self, selector: str, text: str) -> dict[str, Any]:
        """Type text into element by selector."""
        page = await self.get_or_create_page()
        if not page:
            return {"success": False, "error": "No browser page available"}

        try:
            await page.fill(selector, text, timeout=5000)
            return {"success": True, "selector": selector, "text": text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def evaluate_js(self, expression: str) -> Any:
        """Run JavaScript in the browser context."""
        page = await self.get_or_create_page()
        if not page:
            return None
        return await page.evaluate(expression)

    async def close(self):
        """Close browser context."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


# Global browser manager singleton
browser_manager = BrowserManager()
