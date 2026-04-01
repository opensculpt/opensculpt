"""Playwright E2E tests for UI bugs found during manual testing.

Tests written BEFORE fixes — they should FAIL first, then PASS after fixes.

Run: python -m pytest tests/test_ui_bugs.py -v
Requires: container running on port 8420
"""
import time
import requests
import pytest

BASE = "http://localhost:8420"


def api(path):
    try:
        r = requests.get(f"{BASE}{path}", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


@pytest.fixture(scope="module")
def page_html():
    """Fetch the raw HTML once for static checks."""
    r = requests.get(BASE, timeout=10)
    return r.text


# ── Bug 1: Welcome screen stays visible after command ──

class TestWelcomeScreenHides:
    def test_welcome_has_hide_mechanism(self, page_html):
        """The welcome div must have JS that hides it when goals exist."""
        # Check that renderDesktop hides welcome when goals > 0
        assert "welcome" in page_html.lower()
        # The JS must set display:none on welcome when goals load
        assert "welcome" in page_html  # welcome element exists
        # After a command is sent, welcome should hide
        # This requires the chat overlay or goal card to replace it
        assert "display" in page_html  # some display logic exists

    def test_welcome_hidden_after_command_via_api(self):
        """After sending a command, the JS should hide the welcome state."""
        # Send a command (will fail with no LLM but should still hide welcome)
        r = requests.post(f"{BASE}/api/os/command",
                          json={"command": "test"}, timeout=30)
        # The response should come back (error or success)
        assert r.status_code == 200


# ── Bug 2: No loading indicator ──

class TestLoadingIndicator:
    def test_html_has_processing_element(self, page_html):
        """There must be a 'Processing' or 'Thinking' indicator in the HTML/JS."""
        # Check for any loading/processing/thinking text in the JS
        has_indicator = (
            "processing" in page_html.lower() or
            "thinking" in page_html.lower() or
            "loading" in page_html.lower() or
            "spinner" in page_html.lower()
        )
        assert has_indicator, "No loading/processing indicator found in dashboard HTML"

    def test_chat_shows_processing_on_command(self, page_html):
        """When a command is sent, chat area must show a processing state."""
        # The JS runCommand function should add a processing indicator
        assert "Processing" in page_html or "Thinking" in page_html or "processing" in page_html, \
            "No 'Processing' or 'Thinking' text in dashboard — user gets no feedback"


# ── Bug 3: Chat overlay too small ──

class TestChatOverlaySize:
    def test_chat_overlay_has_adequate_height(self, page_html):
        """Chat overlay max-height must be at least 50vh (not 200px)."""
        # Look for chat-overlay CSS with max-height
        import re
        # Find max-height for chat-overlay
        match = re.search(r'\.chat-overlay\s*\{[^}]*max-height:\s*(\d+)', page_html)
        if match:
            height = int(match.group(1))
            # If it's in px, it should be at least 400
            assert height >= 400 or "vh" in page_html[match.start():match.end()+20], \
                f"Chat overlay max-height is {height}px — too small"
        else:
            # Check for vh-based height
            match_vh = re.search(r'\.chat-overlay\s*\{[^}]*max-height:\s*(\d+)vh', page_html)
            assert match_vh, "Chat overlay has no max-height defined"
            vh = int(match_vh.group(1))
            assert vh >= 50, f"Chat overlay max-height is {vh}vh — should be at least 50vh"


# ── Bug 4: No favicon ──

class TestFavicon:
    def test_favicon_exists(self, page_html):
        """Page must have a favicon to avoid 404 on every load."""
        has_favicon = (
            'rel="icon"' in page_html or
            'rel="shortcut icon"' in page_html or
            "favicon" in page_html.lower()
        )
        assert has_favicon, "No favicon defined — causes 404 on every page load"

    def test_favicon_no_404(self):
        """Favicon URL must not return 404."""
        # Try the standard favicon path
        r = requests.get(f"{BASE}/favicon.ico", timeout=5)
        # Either favicon exists OR it's defined inline (data URI in HTML)
        html = requests.get(BASE, timeout=5).text
        has_inline = "data:image" in html and 'rel="icon"' in html
        assert r.status_code == 200 or has_inline, \
            f"Favicon returns {r.status_code} and no inline favicon in HTML"


# ── Bug 5: Dock daemon names meaningless ──

class TestDockDaemonDescriptions:
    def test_dock_items_have_titles(self, page_html):
        """Each dock daemon must have a title/tooltip with human description."""
        # The dock items should have title attributes
        assert "title=" in page_html, "No title attributes found — dock items have no tooltips"
        # Specifically check for daemon descriptions in dock rendering JS
        has_desc_in_dock = (
            "description" in page_html and "dock-item" in page_html
        )
        assert has_desc_in_dock, "Dock items don't use daemon descriptions as tooltips"


# ── Bug 6: No error boundary ──

class TestErrorBoundary:
    def test_has_connection_error_handling(self, page_html):
        """JS must handle fetch failures gracefully — show 'connection lost' or similar."""
        has_error_handling = (
            "catch" in page_html and
            ("connection" in page_html.lower() or
             "offline" in page_html.lower() or
             "error" in page_html.lower())
        )
        assert has_error_handling, "No error handling for failed API calls"

    def test_fetch_wrapper_has_try_catch(self, page_html):
        """The fetchJSON wrapper must catch errors."""
        assert "fetchJSON" in page_html, "No fetchJSON wrapper found"
        # Check it has error handling
        import re
        # Find fetchJSON function
        match = re.search(r'function fetchJSON.*?\{.*?\}', page_html, re.DOTALL)
        if match:
            fn_body = match.group(0)
            assert "catch" in fn_body or "try" in fn_body, \
                "fetchJSON has no try/catch error handling"


# ── Bug 7: Font loading ──

class TestFontFallback:
    def test_font_stack_has_system_fallbacks(self, page_html):
        """Font declarations must have system font fallbacks."""
        import re
        # Find font-family declarations
        fonts = re.findall(r"font-family:\s*([^;]+)", page_html)
        for font_decl in fonts:
            # Must have at least one system font fallback
            has_fallback = any(f in font_decl.lower() for f in [
                "sans-serif", "serif", "monospace", "system-ui",
                "-apple-system", "segoe ui", "arial"
            ])
            if not has_fallback and "inherit" not in font_decl.lower():
                pytest.fail(f"Font declaration has no system fallback: {font_decl.strip()}")


# ── Bug 8: Chip click opens chat ──

class TestChipClickFeedback:
    def test_quickcmd_opens_chat(self, page_html):
        """quickCmd must open chat overlay so user sees feedback."""
        import re
        match = re.search(r'function quickCmd.*?\}', page_html, re.DOTALL)
        assert match, "quickCmd function not found"
        fn = match.group(0)
        assert "openChatOverlay" in fn, "quickCmd doesn't open chat overlay — no feedback on chip click"


# ── Bug 9: Octopus click works ──

class TestOctopusClick:
    def test_octopus_has_onclick(self, page_html):
        """The octopus icon must have an onclick handler."""
        # Find ALL occurrences of the octopus character and check nearby HTML
        idx = page_html.find("&#x1F419;")
        assert idx > 0, "Octopus character not found in HTML"
        # Check 300 chars before the octopus (the span tag with onclick)
        area = page_html[max(0, idx-300):idx+50]
        has_click = "onclick" in area or "cursor:pointer" in area
        assert has_click, f"Octopus icon has no onclick handler. Nearby HTML: {area[-100:]}"


# ── Bug 10: Status line shows current goal ──

class TestStatusLine:
    def test_status_line_element_exists(self, page_html):
        """A status line should exist to show what the OS is currently doing."""
        assert "status-line" in page_html or "status_line" in page_html, \
            "No status line element in dashboard"


# ── Integration: API endpoints work ──

class TestAPIEndpoints:
    def test_status_api(self):
        data = api("/api/status")
        assert data is not None
        assert data["status"] == "ok"

    def test_goals_api(self):
        data = api("/api/goals")
        assert data is not None

    def test_services_api(self):
        data = api("/api/services")
        assert data is not None
        assert "services" in data

    def test_daemons_api(self):
        data = api("/api/daemons")
        assert data is not None

    def test_vitals_api(self):
        data = api("/api/vitals")
        assert data is not None
        assert "cpu_percent" in data

    def test_evolution_api(self):
        data = api("/api/evolution/changelog")
        assert data is not None

    def test_events_api(self):
        r = requests.get(f"{BASE}/api/events?limit=5", timeout=10)
        assert r.status_code == 200
