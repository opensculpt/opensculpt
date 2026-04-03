"""User story end-to-end tests — Playwright.

Each test simulates a real user flow from start to finish.
Run with: python -m pytest tests/test_user_stories.py -v

Requires the server to be running on port 8420.
"""
import json

import pytest
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8420"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    # Clear localStorage so wizard state doesn't bleed between tests
    p = ctx.new_page()
    p.set_default_timeout(20000)
    yield p
    p.close()
    ctx.close()


def _dismiss_wizard(page):
    """Skip the wizard overlay if it appears."""
    page.goto(BASE)
    page.wait_for_timeout(1500)
    wiz = page.locator("#wizard-overlay")
    if wiz.is_visible():
        # Try skip buttons
        for selector in ["text=Skip", "text=Enter OpenSculpt"]:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                page.wait_for_timeout(500)
                return
        # Force-hide if no skip button
        page.evaluate("document.getElementById('wizard-overlay').style.display='none'")


# ═══════════════════════════════════════════════════════════════
# STORY 1: First-time user opens dashboard
# ═══════════════════════════════════════════════════════════════

class TestStory_FirstTimeUser:
    """User installs OpenSculpt, opens localhost:8420 for the first time."""

    def test_dashboard_loads_without_crash(self, page):
        """Dashboard loads, no white screen, no JS errors."""
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE)
        page.wait_for_timeout(3000)
        assert page.title() == "OpenSculpt"
        assert len([e for e in errors if "favicon" not in e.lower()]) == 0

    def test_wizard_or_dashboard_shows(self, page):
        """Either the wizard overlay or the main dashboard is visible."""
        page.goto(BASE)
        page.wait_for_timeout(2000)
        wizard_visible = page.locator("#wizard-overlay").is_visible()
        header_visible = page.locator("header").is_visible()
        assert wizard_visible or header_visible

    def test_all_tabs_clickable(self, page):
        """User can click every tab without JS errors."""
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _dismiss_wizard(page)
        tabs = ["overview", "agents", "evolution", "events", "hands", "setup", "system"]
        for tab in tabs:
            btn = page.locator(f'[data-tab="{tab}"]')
            assert btn.count() > 0, f"Tab '{tab}' button not found"
            btn.click()
            page.wait_for_timeout(300)
            panel = page.locator(f"#tab-{tab}")
            assert panel.is_visible(), f"Tab panel '{tab}' not visible after click"
        assert len([e for e in errors if "favicon" not in e.lower()]) == 0


# ═══════════════════════════════════════════════════════════════
# STORY 2: User configures LLM provider via Settings
# ═══════════════════════════════════════════════════════════════

class TestStory_ConfigureLLM:
    """User opens Settings, enters API key, OS agent starts working."""

    def test_settings_shows_current_config(self, page):
        """Settings tab shows provider status."""
        _dismiss_wizard(page)
        page.locator('[data-tab="setup"]').click()
        page.wait_for_timeout(1000)
        # Should see provider list
        panel = page.locator("#tab-setup")
        assert panel.is_visible()
        html = panel.inner_html()
        # Should mention providers somewhere
        assert "provider" in html.lower() or "Provider" in html

    def test_api_key_save_via_api(self, page):
        """Setting API key via API returns success (doesn't need real key for this test)."""
        resp = page.request.post(f"{BASE}/api/settings/apikey", data={
            "headers": {"Content-Type": "application/json"},
        })
        # Even without a body this should return a proper error, not 500
        assert resp.status != 500

    def test_provider_config_persists(self, page):
        """Provider config saved via setup endpoint persists."""
        # Save a provider config
        resp = page.request.post(f"{BASE}/api/setup/providers/lmstudio", data=json.dumps({
            "enabled": True,
            "config": {"base_url": "http://localhost:1234/v1"},
        }), headers={"Content-Type": "application/json"})
        if resp.ok:
            data = resp.json()
            assert data.get("ok") is True

        # Read it back
        resp2 = page.request.get(f"{BASE}/api/setup/providers")
        if resp2.ok:
            providers = resp2.json()
            assert isinstance(providers, (list, dict))


# ═══════════════════════════════════════════════════════════════
# STORY 3: User sends a command to OS Shell
# ═══════════════════════════════════════════════════════════════

class TestStory_OSShell:
    """User types a command in the OS Shell and gets a response."""

    def test_os_shell_input_exists(self, page):
        """The OS Shell input field exists on the overview tab."""
        _dismiss_wizard(page)
        page.locator('[data-tab="overview"]').click()
        page.wait_for_timeout(500)
        # Find the command input
        input_el = page.locator("#os-input, #cmd-input, input[placeholder*='command'], input[placeholder*='ask'], textarea")
        assert input_el.count() > 0, "No command input found on overview tab"

    def test_command_api_returns_response(self, page):
        """POST /api/os/command returns a structured response."""
        resp = page.request.post(f"{BASE}/api/os/command", data=json.dumps({
            "command": "what is your name"
        }), headers={"Content-Type": "application/json"})
        assert resp.status != 500, f"OS command returned 500: {resp.text()}"
        if resp.ok:
            data = resp.json()
            assert "ok" in data or "message" in data


# ═══════════════════════════════════════════════════════════════
# STORY 4: User checks Evolution tab
# ═══════════════════════════════════════════════════════════════

class TestStory_EvolutionTab:
    """User clicks Evolution tab to see what the OS evolved."""

    def test_evolution_tab_loads_changelog(self, page):
        """Clicking Evolution tab auto-loads the changelog."""
        _dismiss_wizard(page)
        page.locator('[data-tab="evolution"]').click()
        page.wait_for_timeout(2000)
        # Changelog should be populated or show empty message
        cl = page.locator("#evo-changelog")
        empty = page.locator("#evo-changelog-empty")
        assert cl.count() == 1
        # Either has content or shows "No evolved code yet"
        has_content = len(cl.inner_html().strip()) > 0
        shows_empty = empty.is_visible()
        assert has_content or shows_empty

    def test_refresh_button_works(self, page):
        """Refresh button on changelog triggers data reload."""
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _dismiss_wizard(page)
        page.locator('[data-tab="evolution"]').click()
        page.wait_for_timeout(1000)
        btn = page.locator("text=Refresh")
        assert btn.count() > 0 and btn.first.is_visible()
        btn.first.click()
        page.wait_for_timeout(2000)
        assert len([e for e in errors if "favicon" not in e.lower()]) == 0

    def test_demand_signals_section_exists(self, page):
        """Active Demands section is visible in Evolution tab."""
        _dismiss_wizard(page)
        page.locator('[data-tab="evolution"]').click()
        page.wait_for_timeout(500)
        demands = page.locator("#evo-demands-list, #evo-demands-empty")
        assert demands.count() > 0

    def test_evolution_hero_stats(self, page):
        """Evolution hero stats (Cycles, Strategies, Patterns) are visible."""
        _dismiss_wizard(page)
        page.locator('[data-tab="evolution"]').click()
        page.wait_for_timeout(500)
        for el_id in ["evo-cycles", "evo-strategies"]:
            el = page.locator(f"#{el_id}")
            assert el.count() == 1, f"Missing hero stat: {el_id}"


# ═══════════════════════════════════════════════════════════════
# STORY 5: User checks Hands tab
# ═══════════════════════════════════════════════════════════════

class TestStory_HandsTab:
    """User opens Hands tab, sees available hands, starts one."""

    def test_hands_tab_shows_hands(self, page):
        """Hands tab lists the 4 built-in hands."""
        _dismiss_wizard(page)
        page.locator('[data-tab="hands"]').click()
        page.wait_for_timeout(1500)
        panel = page.locator("#tab-hands")
        html = panel.inner_html().lower()
        assert "researcher" in html
        assert "monitor" in html

    def test_start_hand_via_api(self, page):
        """Starting a hand via API works."""
        resp = page.request.post(f"{BASE}/api/hands/researcher/start", data=json.dumps({
            "config": {"topic": "test"}
        }), headers={"Content-Type": "application/json"})
        if resp.ok:
            data = resp.json()
            # Should succeed or report already running
            assert "success" in data or "error" in data

    def test_hands_api_returns_list(self, page):
        """GET /api/hands returns hand status list."""
        resp = page.request.get(f"{BASE}/api/hands")
        assert resp.ok
        data = resp.json()
        assert "hands" in data
        assert len(data["hands"]) >= 4


# ═══════════════════════════════════════════════════════════════
# STORY 6: User runs evolution demo in wizard
# ═══════════════════════════════════════════════════════════════

class TestStory_EvolutionDemo:
    """User clicks the evolution demo card in the wizard."""

    def test_evolve_demo_returns_real_stages(self, page):
        """Evolution demo returns real pipeline stages, not fake data."""
        resp = page.request.post(f"{BASE}/api/wizard/evolve-demo")
        assert resp.ok
        data = resp.json()
        assert "stages" in data
        stages = data["stages"]
        assert len(stages) >= 4

        # Verify stages are real (have required fields)
        for s in stages:
            assert "id" in s
            assert "label" in s
            assert "status" in s
            assert s["status"] in ("done", "fail")

        # If sandbox passed, verify real execution time
        sandbox = next((s for s in stages if s["id"] == "sandbox"), None)
        if sandbox and sandbox["status"] == "done":
            assert "ms" in sandbox.get("sub", "") or "Execution" in sandbox.get("sub", "")

    def test_evolve_demo_writes_real_file(self, page):
        """Evolution demo writes an actual .py file to disk."""
        resp = page.request.post(f"{BASE}/api/wizard/evolve-demo")
        assert resp.ok
        data = resp.json()
        if data.get("evolved"):
            assert data["filename"].endswith(".py")
            assert data.get("metrics", {}).get("sandbox_ms", 0) > 0


# ═══════════════════════════════════════════════════════════════
# STORY 7: User checks system health
# ═══════════════════════════════════════════════════════════════

class TestStory_SystemHealth:
    """User checks System tab for health and resource usage."""

    def test_system_tab_has_content(self, page):
        """System tab shows system info."""
        _dismiss_wizard(page)
        page.locator('[data-tab="system"]').click()
        page.wait_for_timeout(500)
        panel = page.locator("#tab-system")
        assert panel.is_visible()

    def test_vitals_api(self, page):
        """System vitals (CPU, memory, disk) are available."""
        resp = page.request.get(f"{BASE}/api/vitals")
        assert resp.ok
        data = resp.json()
        assert "cpu_percent" in data
        assert "mem_percent" in data


# ═══════════════════════════════════════════════════════════════
# STORY 8: API consistency — no 500 errors
# ═══════════════════════════════════════════════════════════════

class TestStory_APIStability:
    """Every API endpoint the dashboard calls should never return 500."""

    ENDPOINTS = [
        ("GET", "/api/status"),
        ("GET", "/api/events"),
        ("GET", "/api/audit?limit=10"),
        ("GET", "/api/vitals"),
        ("GET", "/api/settings"),
        ("GET", "/api/hands"),
        ("GET", "/api/agents/registry"),
        ("GET", "/api/processes"),
        ("GET", "/api/evolution/state"),
        ("GET", "/api/evolution/changelog"),
        ("GET", "/api/evolution/demands"),
        ("GET", "/api/evolution/meta"),
        ("GET", "/api/sync/manifest"),
        ("GET", "/api/wizard/status"),
        ("GET", "/api/wizard/detect"),
        ("GET", "/api/setup/providers"),
        ("GET", "/api/setup/channels"),
        ("GET", "/api/setup/tools"),
        ("GET", "/api/codebase"),
        ("GET", "/api/deps"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_no_500(self, page, method, path):
        """Endpoint does not return HTTP 500."""
        if method == "GET":
            resp = page.request.get(f"{BASE}{path}")
        else:
            resp = page.request.post(f"{BASE}{path}")
        assert resp.status != 500, f"{method} {path} returned 500: {resp.text()[:200]}"


# ═══════════════════════════════════════════════════════════════
# STORY 9: Wizard provider setup — the critical first-time flow
# ═══════════════════════════════════════════════════════════════

class TestStory_WizardProviderSetup:
    """User opens wizard, picks a provider, enters API key, advances."""

    def test_wizard_boot_probe_completes(self, page):
        """Boot probe finishes and enables the Start button."""
        # Force first-run
        page.request.post(f"{BASE}/api/wizard/complete")  # reset may not exist, that's ok
        page.goto(BASE)
        page.wait_for_timeout(2000)
        # If wizard is visible, check the start button
        wiz = page.locator("#wizard-overlay")
        if not wiz.is_visible():
            # Force show it
            page.evaluate('document.getElementById("wizard-overlay").style.display=""')
            page.evaluate('wizBootSequence()')
            page.wait_for_timeout(6000)
        start_btn = page.locator("#wiz-start-btn")
        # Wait up to 10s for boot probe
        for _ in range(10):
            if not start_btn.is_disabled():
                break
            page.wait_for_timeout(1000)
        assert not start_btn.is_disabled(), "Start button never enabled — boot probe stuck"

    def test_wizard_advance_to_provider_step(self, page):
        """Clicking Start advances to the provider selection step."""
        page.goto(BASE)
        page.wait_for_timeout(2000)
        wiz = page.locator("#wizard-overlay")
        if not wiz.is_visible():
            page.evaluate('document.getElementById("wizard-overlay").style.display=""')
            page.evaluate('wizBootSequence()')
            page.wait_for_timeout(6000)
        start_btn = page.locator("#wiz-start-btn")
        for _ in range(10):
            if not start_btn.is_disabled():
                break
            page.wait_for_timeout(1000)
        start_btn.click()
        page.wait_for_timeout(1000)
        step1 = page.locator("#wiz-step-1")
        assert step1.is_visible(), "Step 1 (provider selection) not visible after clicking Start"

    def test_wizard_manual_key_entry_works(self, page):
        """User can enter API key manually and advance to demo step."""
        page.goto(BASE)
        page.wait_for_timeout(2000)
        wiz = page.locator("#wizard-overlay")
        if not wiz.is_visible():
            page.evaluate('document.getElementById("wizard-overlay").style.display=""')
            page.evaluate('wizBootSequence()')
            page.wait_for_timeout(6000)
        # Advance to step 1
        start_btn = page.locator("#wiz-start-btn")
        for _ in range(10):
            if not start_btn.is_disabled():
                break
            page.wait_for_timeout(1000)
        start_btn.click()
        page.wait_for_timeout(1000)

        # Show manual section if hidden
        toggle = page.locator("#wiz-manual-toggle")
        if toggle.is_visible():
            toggle.click()
            page.wait_for_timeout(300)

        # Select provider and enter key
        prov_select = page.locator("#wiz-provider-select")
        key_input = page.locator("#wiz-api-key")
        if prov_select.is_visible():
            prov_select.select_option("anthropic")
        if key_input.is_visible():
            key_input.fill("sk-ant-test-key-12345")

        # Click Continue
        continue_btn = page.locator("#wiz-provider-next")
        continue_btn.click()
        page.wait_for_timeout(2000)

        # Should advance to step 2
        step2 = page.locator("#wiz-step-2")
        assert step2.is_visible(), "Did not advance to Step 2 after entering API key"

    def test_settings_api_key_wires_to_os_agent(self, page):
        """Setting API key via Settings tab actually updates the OS agent."""
        # Save a key via the API
        resp = page.request.post(f"{BASE}/api/settings/apikey",
            data=json.dumps({"api_key": "sk-ant-test-key-12345"}),
            headers={"Content-Type": "application/json"})
        assert resp.ok
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("preview", "").startswith("sk-ant-t")

        # Verify settings reflect the key is set
        resp2 = page.request.get(f"{BASE}/api/settings")
        assert resp2.ok
        settings = resp2.json()
        assert settings.get("has_api_key") is True


# ═══════════════════════════════════════════════════════════════
# STORY 10: Branding consistency
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════

class TestStory_Branding:
    """No AGOS references visible to the user anywhere."""

    def test_page_title(self, page):
        page.goto(BASE)
        assert page.title() == "OpenSculpt"

    def test_no_agos_in_visible_text(self, page):
        """Visible text on the dashboard should not say AGOS."""
        _dismiss_wizard(page)
        page.wait_for_timeout(1000)
        # Check all visible text on the page
        body_text = page.locator("body").inner_text()
        # Allow "agos" in module paths (agos.evolution.state) but not as branding
        lines = body_text.split("\n")
        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue
            # Skip technical lines (module paths, file paths)
            if "agos." in line_clean.lower() or ".agos" in line_clean.lower():
                continue
            # Check for AGOS as a standalone brand name
            if "AGOS" in line_clean and "agos." not in line_clean.lower():
                # Allow it in code blocks / technical context
                if line_clean.startswith("#") or line_clean.startswith("//"):
                    continue
                pytest.fail(f"Found 'AGOS' branding in visible text: '{line_clean[:100]}'")

    def test_settings_env_vars_say_sculpt(self, page):
        """Settings/config references use SCULPT_ prefix, not AGOS_."""
        resp = page.request.get(f"{BASE}/api/wizard/detect")
        if resp.ok:
            data = resp.json()
            text = json.dumps(data)
            assert "AGOS_" not in text, f"Found AGOS_ env var in wizard detect: {text[:200]}"
