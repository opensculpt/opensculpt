"""Browser tool — web UI interaction via Playwright.

Allows the OS agent to navigate websites, fill forms, click buttons,
read page content, and take screenshots. Essential for interacting
with web applications (CRM setup, dashboards, admin panels).
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# Singleton browser instance (reused across calls)
_browser = None
_page = None


async def _ensure_browser():
    """Launch browser if not already running."""
    global _browser, _page
    if _page and not _page.is_closed():
        return _page
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(headless=True)
        _page = await _browser.new_page()
        _page.set_default_timeout(15000)
        return _page
    except Exception:
        return None


async def browse(url: str) -> str:
    """Navigate to a URL and return the page text content.

    Args:
        url: Full URL to navigate to (e.g. "http://localhost:8081")
    """
    page = await _ensure_browser()
    if not page:
        return "ERROR: Playwright not available. Install with: pip install playwright && python -m playwright install chromium"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        title = await page.title()
        text = await page.inner_text("body")
        # Truncate for token efficiency
        text = text[:3000] if len(text) > 3000 else text
        return f"Page: {title}\nURL: {page.url}\n\n{text}"
    except Exception as e:
        return f"ERROR navigating to {url}: {e}"


async def browser_fill(selector: str, value: str) -> str:
    """Fill a form field.

    Args:
        selector: CSS selector for the input (e.g. "#username", "input[name='email']")
        value: Text to type into the field
    """
    page = await _ensure_browser()
    if not page:
        return "ERROR: Browser not available"
    try:
        await page.fill(selector, value)
        return f"Filled '{selector}' with '{value[:50]}'"
    except Exception as e:
        return f"ERROR filling {selector}: {e}"


async def browser_click(selector: str) -> str:
    """Click an element on the page.

    Args:
        selector: CSS selector (e.g. "button[type='submit']", "#login-btn", "text=Sign In")
    """
    page = await _ensure_browser()
    if not page:
        return "ERROR: Browser not available"
    try:
        await page.click(selector, timeout=10000)
        await page.wait_for_timeout(1000)
        title = await page.title()
        return f"Clicked '{selector}'. Page now: {title} ({page.url})"
    except Exception as e:
        return f"ERROR clicking {selector}: {e}"


async def browser_screenshot(path: str = "/tmp/screenshot.png") -> str:
    """Take a screenshot of the current page.

    Args:
        path: File path to save the screenshot
    """
    page = await _ensure_browser()
    if not page:
        return "ERROR: Browser not available"
    try:
        await page.screenshot(path=path, full_page=False)
        return f"Screenshot saved to {path}"
    except Exception as e:
        return f"ERROR taking screenshot: {e}"


async def browser_content(selector: str = "body") -> str:
    """Get text content of an element.

    Args:
        selector: CSS selector (default "body" for full page)
    """
    page = await _ensure_browser()
    if not page:
        return "ERROR: Browser not available"
    try:
        text = await page.inner_text(selector)
        return text[:5000]
    except Exception as e:
        return f"ERROR reading {selector}: {e}"


async def browser_eval(js: str) -> str:
    """Run JavaScript on the page and return the result.

    Args:
        js: JavaScript code to evaluate
    """
    page = await _ensure_browser()
    if not page:
        return "ERROR: Browser not available"
    try:
        result = await page.evaluate(js)
        return str(result)[:2000]
    except Exception as e:
        return f"ERROR evaluating JS: {e}"
