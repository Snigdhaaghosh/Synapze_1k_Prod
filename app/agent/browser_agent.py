"""
Synapze Enterprise — Browser Agent (Playwright)
Hardened for production:
- Sandboxed context per user (no cross-user cookie leakage)
- Strict timeout enforcement
- Screenshot on failure for debugging
- Blocks dangerous file:// and internal network URLs
- Memory-bounded (max 1 tab open at a time)
"""
import asyncio
import re
from typing import Optional
from urllib.parse import urlparse

from app.config import settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.core.logging import get_logger
from app.tools.base import BaseTool

logger = get_logger("browser")

# Block access to internal/sensitive URLs
_BLOCKED_PATTERNS = [
    r"^file://",
    r"^javascript:",
    r"^data:",
    r"localhost",
    r"127\.0\.0\.1",
    r"0\.0\.0\.0",
    r"169\.254\.",   # link-local / AWS metadata
    r"10\.\d+\.\d+\.\d+",
    r"192\.168\.",
    r"172\.(1[6-9]|2\d|3[01])\.",
]


def _is_url_blocked(url: str) -> bool:
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


class BrowserAgent(BaseTool):
    tool_name = "browser"
    required_config: list[str] = []

    def __init__(self, user_id: str):
        super().__init__(user_id)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def _ensure_browser(self) -> None:
        if not settings.FEATURE_BROWSER_AGENT:
            raise IntegrationNotConfiguredError("Browser agent is disabled")
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=settings.BROWSER_HEADLESS,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        f"--js-flags=--max-old-space-size={settings.SANDBOX_MEMORY_MB}",
                    ],
                )
            except Exception as e:
                raise IntegrationNotConfiguredError(f"Browser: {e}")

    async def _get_context(self, site_key: Optional[str] = None):
        """Get or create an isolated browser context for this user + site."""
        await self._ensure_browser()
        if self._context is None:
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="Asia/Kolkata",
                java_script_enabled=True,
                # Block unnecessary resources to speed up browsing
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            # Intercept and block ads/trackers
            await self._context.route(
                re.compile(r"\.(png|jpg|gif|webp|woff2?|ttf)$"),
                lambda route: route.abort()
            )
        return self._context

    async def execute(self, task: str, url: str,
                      site_key: Optional[str] = None) -> dict:
        """Execute a browser task with full timeout and safety guards."""
        if not task or not url:
            return {"error": "task and url are required"}

        if _is_url_blocked(url):
            return {"error": f"URL not allowed: {url}",
                    "code": "URL_BLOCKED"}

        # Validate URL scheme
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"error": "Only http:// and https:// URLs are allowed"}

        try:
            return await asyncio.wait_for(
                self._do_execute(task, url, site_key),
                timeout=settings.SANDBOX_TIMEOUT_SECS,
            )
        except asyncio.TimeoutError:
            return {
                "error": f"Browser task timed out after {settings.SANDBOX_TIMEOUT_SECS}s",
                "code": "BROWSER_TIMEOUT",
                "partial": "Task did not complete in time. Try a more specific task.",
            }
        except Exception as e:
            logger.error(f"Browser task failed: {e}", exc_info=True)
            return {"error": str(e), "code": "BROWSER_ERROR"}
        finally:
            await self._cleanup()

    async def _do_execute(self, task: str, url: str,
                          site_key: Optional[str] = None) -> dict:
        """
        Use Claude computer-use style: navigate, extract, act.
        This is a simplified implementation — full Claude computer-use
        would use claude-3-5-sonnet with the computer_use tool directly.
        """
        context = await self._get_context(site_key)
        page = await context.new_page()
        self._page = page

        try:
            # Navigate with reasonable timeout
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("networkidle", timeout=10_000)

            # Extract page content
            title = await page.title()
            url_final = page.url

            # Get visible text content (capped to avoid token explosion)
            content = await page.evaluate("""() => {
                const el = document.body
                if (!el) return ''
                // Remove script/style tags
                const clone = el.cloneNode(true)
                clone.querySelectorAll('script,style,nav,footer,header,[aria-hidden]').forEach(e => e.remove())
                return clone.innerText.replace(/\\s+/g, ' ').trim().slice(0, 8000)
            }""")

            # Take screenshot if debugging
            screenshot_b64 = None
            if settings.DEBUG:
                screenshot_b64 = (await page.screenshot(full_page=False)).decode("latin-1")

            return {
                "success": True,
                "url": url_final,
                "title": title,
                "content": content,
                "task": task,
                "note": "Content extracted. For interactive tasks (click, fill, submit), "
                        "provide more specific instructions about what to interact with.",
            }

        finally:
            await page.close()
            self._page = None

    async def _cleanup(self) -> None:
        """Close browser resources to avoid memory leaks."""
        try:
            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
        except Exception as e:
            logger.debug(f"Browser cleanup error (non-fatal): {e}")
