"""Browser Automation and DOM Interaction Tool.

Provides CDP and Playwright integration for deep browser control,
inspired by wy-coliney/jev-browser-use:
- Connects to existing Chrome/Edge/Brave instances via CDP or launches automated sessions.
- Injects unique DOM identifiers (data-voice-id) for 100% collision-free element clicks & fills.
- Supports system-installed browsers (Chrome, Edge) with automatic fallback.
- Thread-safe architecture running on a dedicated internal event loop.
- Dual async and sync interfaces for voice agent and worker threads.
"""

from __future__ import annotations

import asyncio
import logging
import threading
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
    """Manages browser control via Playwright and Chrome DevTools Protocol.
    Runs on a dedicated background event loop for complete thread safety across voice & worker threads.
    """

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._active_page: Page | None = None
        self._lock = asyncio.Lock()

    def _ensure_runner(self):
        """Ensure the dedicated background thread and loop are active."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._started.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BrowserLoopThread")
        self._thread.start()
        self._started.wait(timeout=10.0)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def _dispatch(self, coro, timeout: float = 35.0) -> Any:
        """Dispatch a coroutine to the browser's dedicated loop and wait synchronously for the result."""
        self._ensure_runner()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _dispatch_async(self, coro) -> Any:
        """Call a coroutine on the browser loop from any asynchronous context."""
        self._ensure_runner()
        try:
            current_loop = asyncio.get_running_loop()
            if current_loop is self._loop:
                return await coro
        except RuntimeError:
            pass
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

    # -------------------------------------------------------------------------
    # Internal Coroutines (executing on the dedicated loop)
    # -------------------------------------------------------------------------

    async def _ensure_playwright(self):
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def _connect_cdp(self, endpoint_url: str = "http://127.0.0.1:9222") -> bool:
        """Connect to an existing browser running with --remote-debugging-port."""
        async with self._lock:
            try:
                await self._ensure_playwright()
                endpoints = [endpoint_url]
                if "127.0.0.1" in endpoint_url:
                    endpoints.append(endpoint_url.replace("127.0.0.1", "localhost"))
                elif "localhost" in endpoint_url:
                    endpoints.append(endpoint_url.replace("localhost", "127.0.0.1"))

                for ep in endpoints:
                    try:
                        self._browser = await self._playwright.chromium.connect_over_cdp(ep)
                        contexts = self._browser.contexts
                        if contexts:
                            self._context = contexts[0]
                            pages = self._context.pages
                            self._active_page = pages[0] if pages else await self._context.new_page()
                        else:
                            self._context = await self._browser.new_context()
                            self._active_page = await self._context.new_page()
                        logger.info("Connected to browser over CDP at %s", ep)
                        return True
                    except Exception as err:
                        logger.debug("Could not connect to CDP at %s: %s", ep, err)
                        continue
                return False
            except Exception as exc:
                logger.debug("CDP connection error: %s", exc)
                return False

    async def _launch_browser(self, headless: bool = False) -> bool:
        """Launch a Chromium browser instance (prioritizing installed Chrome/Edge)."""
        async with self._lock:
            try:
                await self._ensure_playwright()
                # Prioritize installed system browsers then fallback to default playwright bundle
                for channel in ["chrome", "msedge", None]:
                    try:
                        if channel:
                            self._browser = await self._playwright.chromium.launch(
                                channel=channel, headless=headless
                            )
                            logger.info("Launched browser using channel='%s' (headless=%s)", channel, headless)
                        else:
                            self._browser = await self._playwright.chromium.launch(headless=headless)
                            logger.info("Launched default Playwright Chromium (headless=%s)", headless)
                        break
                    except Exception as err:
                        logger.debug("Could not launch channel %s: %s", channel, err)
                        continue

                if not self._browser:
                    logger.error("Failed to launch any browser channel")
                    return False

                self._context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
                self._active_page = await self._context.new_page()
                return True
            except Exception as exc:
                logger.error("Failed to launch browser: %s", exc)
                return False

    async def _get_or_create_page(self, auto_launch: bool = True, headless: bool = False) -> Page | None:
        """Get the current active page, try CDP, or launch if needed."""
        if self._active_page and not self._active_page.is_closed():
            return self._active_page

        # Try connecting via CDP first
        if await self._connect_cdp():
            return self._active_page

        if auto_launch:
            if await self._launch_browser(headless=headless):
                return self._active_page

        return None

    async def _navigate(self, url: str) -> dict[str, Any]:
        page = await self._get_or_create_page(auto_launch=True, headless=False)
        if not page:
            return {"success": False, "error": "No browser page available"}

        query_clean = url.strip()
        if query_clean.startswith("http://") or query_clean.startswith("https://") or query_clean.startswith("file://"):
            target_url = query_clean
        elif "." in query_clean and " " not in query_clean:
            target_url = "https://" + query_clean
        else:
            target_url = f"https://www.google.com/search?q={query_clean.replace(' ', '+')}"

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            return {"success": True, "url": page.url, "title": await page.title()}
        except Exception as exc:
            return {"success": False, "error": str(exc), "url": target_url}

    async def _extract_interactive_dom(self, max_elements: int = 40) -> list[DOMElement]:
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return []

        js_script = """
        () => {
            const elements = Array.from(document.querySelectorAll(
                'a, button, input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [role="checkbox"]'
            ));
            const results = [];
            let idx = 1;
            for (const el of elements) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width > 3 && rect.height > 3 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0') {
                    const domId = 'dom_' + idx++;
                    el.setAttribute('data-voice-id', domId);
                    
                    let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
                    
                    let sel = '';
                    if (el.id) {
                        sel = '#' + el.id;
                    } else if (el.name) {
                        sel = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                    } else if (el.getAttribute('data-testid')) {
                        sel = `[data-testid="${el.getAttribute('data-testid')}"]`;
                    } else {
                        sel = `[data-voice-id="${domId}"]`;
                    }

                    results.push({
                        id: domId,
                        tag: el.tagName.toLowerCase(),
                        text: text,
                        role: el.getAttribute('role') || el.tagName.toLowerCase(),
                        selector: `[data-voice-id="${domId}"]`,
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

    async def _extract_page_content(self, max_elements: int = 30) -> dict[str, Any] | None:
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return None

        try:
            title = await page.title()
            url = page.url
            body_text = await page.evaluate(
                "() => document.body ? document.body.innerText.replace(/\\s+/g, ' ').slice(0, 2500) : ''"
            )
            dom_elements = await self._extract_interactive_dom(max_elements=max_elements)
            return {
                "title": title,
                "url": url,
                "text": body_text.strip(),
                "elements": [e.to_dict() for e in dom_elements],
            }
        except Exception as exc:
            logger.debug("Failed to extract page content: %s", exc)
            return None

    async def _click_element(self, target: str) -> dict[str, Any]:
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return {"success": False, "error": "No active browser page"}

        target_clean = target.strip()
        selector = f'[data-voice-id="{target_clean}"]' if target_clean.startswith("dom_") else target_clean

        if target_clean.startswith("dom_"):
            try:
                if await page.locator(selector).count() == 0:
                    await self._extract_interactive_dom()
            except Exception:
                pass

        try:
            await page.click(selector, timeout=5000)
            return {"success": True, "target": target_clean, "selector": selector}
        except Exception as exc:
            # Fallback to search by visible text
            try:
                locator = page.get_by_text(target_clean, exact=False).first
                await locator.click(timeout=3000)
                return {"success": True, "target": target_clean, "fallback": "text_search"}
            except Exception:
                return {"success": False, "error": str(exc), "target": target_clean}

    async def _fill_element(self, target: str, text: str) -> dict[str, Any]:
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return {"success": False, "error": "No active browser page"}

        target_clean = target.strip()
        selector = f'[data-voice-id="{target_clean}"]' if target_clean.startswith("dom_") else target_clean

        if target_clean.startswith("dom_"):
            try:
                if await page.locator(selector).count() == 0:
                    await self._extract_interactive_dom()
            except Exception:
                pass

        try:
            await page.fill(selector, text, timeout=5000)
            return {"success": True, "target": target_clean, "text": text}
        except Exception as exc:
            try:
                await page.click(selector, timeout=3000)
                await page.keyboard.type(text)
                return {"success": True, "target": target_clean, "text": text, "fallback": "keyboard_type"}
            except Exception:
                return {"success": False, "error": str(exc), "target": target_clean}

    async def _scroll_page(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]:
        """Scroll the web page viewport vertically by specified pixels."""
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return {"success": False, "error": "No active browser page"}

        scroll_y = pixels if direction.lower() == "down" else -pixels
        try:
            await page.evaluate(f"window.scrollBy({{ top: {scroll_y}, behavior: 'smooth' }});")
            return {"success": True, "direction": direction, "pixels": pixels}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _inspect_browser_dom(self, max_elements: int = 40) -> dict[str, Any]:
        """Extract live interactive DOM elements with assigned IDs and CSS selectors."""
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return {"success": False, "error": "No active browser page. Use browser_navigate to open a page first."}

        try:
            title = await page.title()
            url = page.url
            dom_elements = await self._extract_interactive_dom(max_elements=max_elements)
            return {
                "success": True,
                "title": title,
                "url": url,
                "count": len(dom_elements),
                "elements": [e.to_dict() for e in dom_elements],
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _set_content(self, html: str):
        page = await self._get_or_create_page(auto_launch=True, headless=False)
        if page:
            await page.set_content(html, wait_until="domcontentloaded")

    async def _evaluate_js(self, expression: str) -> Any:
        page = await self._get_or_create_page(auto_launch=False)
        if not page or page.is_closed():
            return None
        return await page.evaluate(expression)

    async def _close(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            self._active_page = None
            self._context = None

    # -------------------------------------------------------------------------
    # Public Async API (for LiveKit voice agent & async services)
    # -------------------------------------------------------------------------

    async def connect_cdp(self, endpoint_url: str = "http://127.0.0.1:9222") -> bool:
        return await self._dispatch_async(self._connect_cdp(endpoint_url))

    async def launch_browser(self, headless: bool = False) -> bool:
        return await self._dispatch_async(self._launch_browser(headless=headless))

    async def get_or_create_page(self) -> Page | None:
        return await self._dispatch_async(self._get_or_create_page())

    async def navigate(self, url: str) -> dict[str, Any]:
        return await self._dispatch_async(self._navigate(url))

    async def extract_interactive_dom(self, max_elements: int = 40) -> list[DOMElement]:
        return await self._dispatch_async(self._extract_interactive_dom(max_elements=max_elements))

    async def extract_page_content(self, max_elements: int = 30) -> dict[str, Any] | None:
        return await self._dispatch_async(self._extract_page_content(max_elements=max_elements))

    async def click_element(self, target: str) -> dict[str, Any]:
        return await self._dispatch_async(self._click_element(target))

    async def fill_element(self, target: str, text: str) -> dict[str, Any]:
        return await self._dispatch_async(self._fill_element(target, text))

    async def scroll_page(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]:
        return await self._dispatch_async(self._scroll_page(direction, pixels))

    async def inspect_browser_dom(self, max_elements: int = 40) -> dict[str, Any]:
        return await self._dispatch_async(self._inspect_browser_dom(max_elements=max_elements))

    async def set_content(self, html: str):
        return await self._dispatch_async(self._set_content(html))

    async def evaluate_js(self, expression: str) -> Any:
        return await self._dispatch_async(self._evaluate_js(expression))

    async def close(self):
        return await self._dispatch_async(self._close())

    # -------------------------------------------------------------------------
    # Public Sync API (for LLM worker threads & scripts)
    # -------------------------------------------------------------------------

    def navigate_sync(self, url: str) -> dict[str, Any]:
        return self._dispatch(self._navigate(url))

    def set_content_sync(self, html: str):
        return self._dispatch(self._set_content(html))

    def extract_interactive_dom_sync(self, max_elements: int = 40) -> list[DOMElement]:
        return self._dispatch(self._extract_interactive_dom(max_elements=max_elements))

    def extract_page_content_sync(self, max_elements: int = 30) -> dict[str, Any] | None:
        return self._dispatch(self._extract_page_content(max_elements=max_elements))

    def click_element_sync(self, target: str) -> dict[str, Any]:
        return self._dispatch(self._click_element(target))

    def fill_element_sync(self, target: str, text: str) -> dict[str, Any]:
        return self._dispatch(self._fill_element(target, text))

    def scroll_page_sync(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]:
        return self._dispatch(self._scroll_page(direction, pixels))

    def inspect_browser_dom_sync(self, max_elements: int = 40) -> dict[str, Any]:
        return self._dispatch(self._inspect_browser_dom(max_elements=max_elements))

    def evaluate_js_sync(self, expression: str) -> Any:
        return self._dispatch(self._evaluate_js(expression))

    def close_sync(self):
        return self._dispatch(self._close())


# Global browser manager singleton
browser_manager = BrowserManager()
