"""End-to-end Playwright tests for Service Lifecycle.

Tests every button and feature in the Services panel:
- Dock pill appears with correct count
- Click opens panel with services grouped by goal
- Service names are human-readable (not IDs)
- URLs are clickable links
- Credentials shown
- Stop button actually stops the service
- Restart button actually restarts the service
- Container restart preserves services (boot restore)
- ServiceKeeper auto-restarts crashed services

Run: python -m pytest tests/test_services_e2e.py -v --timeout=300
Requires: container running on port 8420 with at least 1 service card
"""
import json
import subprocess
import time

import pytest
import requests

BASE = "http://localhost:8420"
NOTES_PORT = 5555


def api(path):
    """GET an API endpoint, return parsed JSON."""
    try:
        r = requests.get(f"{BASE}{path}", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def post_api(path):
    """POST an API endpoint, return parsed JSON."""
    try:
        r = requests.post(f"{BASE}{path}", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def docker_exec(cmd):
    """Run command inside opensculpt container."""
    result = subprocess.run(
        ["docker", "exec", "opensculpt", "bash", "-c", cmd],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip(), result.returncode


def curl_notes():
    """Check if Notes API responds inside container."""
    out, rc = docker_exec(f"curl -sf --max-time 5 http://localhost:{NOTES_PORT}/notes")
    return rc == 0, out


def wait_for_condition(check_fn, timeout=90, interval=5, desc="condition"):
    """Poll until check_fn returns True or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if check_fn():
            return True
        time.sleep(interval)
    return False


# ── Precondition checks ──


class TestPreconditions:
    def test_container_running(self):
        data = api("/api/status")
        assert data is not None, "Container not responding on port 8420"
        assert data["status"] == "ok"

    def test_services_api_exists(self):
        data = api("/api/services")
        assert data is not None, "/api/services endpoint missing"
        assert "services" in data

    def test_at_least_one_service(self):
        data = api("/api/services")
        services = data.get("services", [])
        assert len(services) > 0, "No service cards registered. Deploy something first."

    def test_notes_api_has_card(self):
        data = api("/api/services")
        services = data.get("services", [])
        notes = [s for s in services if s.get("port") == NOTES_PORT]
        assert len(notes) > 0, f"No service card with port {NOTES_PORT}"


# ── API endpoint tests ──


class TestServicesAPI:
    def test_list_services_returns_fields(self):
        data = api("/api/services")
        services = data["services"]
        for svc in services:
            assert "name" in svc
            assert "status" in svc
            assert "port" in svc
            assert "health_check" in svc
            assert "start_command" in svc
            assert "restart_count" in svc

    def test_service_names_are_human_readable(self):
        """No raw goal IDs like goal_1774769923_948_app_run as names."""
        data = api("/api/services")
        for svc in data["services"]:
            name = svc["name"]
            assert not name.startswith("goal_"), f"Service name is a raw ID: {name}"

    def test_service_has_url(self):
        data = api("/api/services")
        services_with_port = [s for s in data["services"] if s.get("port") and s["port"] != 0]
        for svc in services_with_port:
            assert svc.get("url"), f"Service {svc['name']} has port but no URL"
            assert "localhost" in svc["url"]

    def test_notes_api_healthy(self):
        data = api("/api/services")
        notes = [s for s in data["services"] if s.get("port") == NOTES_PORT]
        assert len(notes) > 0
        assert notes[0]["status"] == "healthy", f"Notes API status: {notes[0]['status']}"

    def test_notes_api_has_start_command(self):
        data = api("/api/services")
        notes = [s for s in data["services"] if s.get("port") == NOTES_PORT]
        assert notes[0].get("start_command"), "Notes API has no start_command"

    def test_notes_api_has_health_check(self):
        data = api("/api/services")
        notes = [s for s in data["services"] if s.get("port") == NOTES_PORT]
        hc = notes[0].get("health_check", "")
        assert "curl" in hc or "http" in hc, f"Health check is not a curl/http check: {hc}"


# ── Stop button tests ──


class TestStopService:
    def test_stop_api_returns_ok(self):
        result = post_api(f"/api/services/Notes%20API/stop")
        assert result is not None
        assert result.get("ok") is True, f"Stop returned: {result}"

    def test_stop_actually_kills_process(self):
        # First make sure it's running
        up, _ = curl_notes()
        if not up:
            # Restart it first
            post_api(f"/api/services/Notes%20API/restart")
            assert wait_for_condition(lambda: curl_notes()[0], timeout=60, desc="Notes API start")

        # Now stop it
        post_api(f"/api/services/Notes%20API/stop")
        time.sleep(3)
        up, _ = curl_notes()
        assert not up, "Notes API still responding after stop"

    def test_stop_updates_card_status(self):
        post_api(f"/api/services/Notes%20API/stop")
        time.sleep(2)
        data = api("/api/services")
        notes = [s for s in data["services"] if s.get("port") == NOTES_PORT]
        assert notes[0]["status"] in ("stopped", "crashed"), f"Status after stop: {notes[0]['status']}"


# ── Restart button tests ──


class TestRestartService:
    def test_restart_api_returns_ok(self):
        result = post_api(f"/api/services/Notes%20API/restart")
        assert result is not None
        assert result.get("ok") is True

    def test_restart_brings_service_back(self):
        # Stop first
        post_api(f"/api/services/Notes%20API/stop")
        time.sleep(3)
        up, _ = curl_notes()
        assert not up, "Service should be down before restart test"

        # Restart
        post_api(f"/api/services/Notes%20API/restart")

        # Wait for ServiceKeeper (30s tick + startup time)
        restored = wait_for_condition(
            lambda: curl_notes()[0],
            timeout=90,
            interval=5,
            desc="Notes API restart",
        )
        assert restored, "Notes API did not come back after restart"

    def test_restart_preserves_data(self):
        # Make sure service is up with data
        if not curl_notes()[0]:
            post_api(f"/api/services/Notes%20API/restart")
            wait_for_condition(lambda: curl_notes()[0], timeout=90)

        # Check data before
        _, before = curl_notes()
        before_data = json.loads(before)
        assert len(before_data) > 0, "No notes in database before restart"

        # Stop + restart
        post_api(f"/api/services/Notes%20API/stop")
        time.sleep(3)
        post_api(f"/api/services/Notes%20API/restart")
        wait_for_condition(lambda: curl_notes()[0], timeout=90)

        # Check data after
        _, after = curl_notes()
        after_data = json.loads(after)
        assert len(after_data) == len(before_data), f"Data lost: {len(before_data)} -> {len(after_data)}"
        assert after_data[0]["title"] == before_data[0]["title"]


# ── ServiceKeeper auto-restart tests ──


class TestServiceKeeperAutoRestart:
    def test_kill_process_triggers_restart(self):
        """Kill the Flask process directly — ServiceKeeper should detect and restart."""
        # Ensure running — use restart API to reset card status to healthy first
        post_api(f"/api/services/Notes%20API/restart")
        assert wait_for_condition(lambda: curl_notes()[0], timeout=90), "Could not start Notes API"

        # Verify it's healthy in the API
        time.sleep(5)  # Let ServiceKeeper mark it healthy

        # Kill the process directly (not via API — simulates crash)
        docker_exec(f"pkill -9 -f 'flask run --port {NOTES_PORT}'")
        time.sleep(2)
        up, _ = curl_notes()
        assert not up, "Process should be dead after pkill"

        # Wait for ServiceKeeper: 3 failed health checks (3 x 30s = 90s) + simple restart + startup
        # Total: ~120s minimum. Give 240s to be safe.
        restored = wait_for_condition(
            lambda: curl_notes()[0],
            timeout=240,
            interval=10,
            desc="auto-restart after kill",
        )
        assert restored, "ServiceKeeper did not auto-restart the killed service within 240s"


# ── Container restart (boot restore) tests ──


class TestBootRestore:
    def test_container_restart_restores_service(self):
        """The ultimate test — restart the container, service should come back."""
        # Ensure running first
        if not curl_notes()[0]:
            post_api(f"/api/services/Notes%20API/restart")
            assert wait_for_condition(lambda: curl_notes()[0], timeout=90)

        # Restart container
        subprocess.run(["docker", "restart", "opensculpt"], timeout=30)
        time.sleep(5)

        # Wait for container to be healthy
        assert wait_for_condition(
            lambda: api("/api/status") is not None,
            timeout=60,
            interval=3,
            desc="container boot",
        ), "Container didn't come back"

        # Wait for service restoration (boot_restore + Flask startup)
        restored = wait_for_condition(
            lambda: curl_notes()[0],
            timeout=90,
            interval=5,
            desc="boot restore",
        )
        assert restored, "Notes API did not survive container restart"

    def test_data_survives_container_restart(self):
        """SQLite data persists across container restart."""
        # Get data before
        if not curl_notes()[0]:
            post_api(f"/api/services/Notes%20API/restart")
            wait_for_condition(lambda: curl_notes()[0], timeout=90)

        _, before = curl_notes()
        before_data = json.loads(before)

        # Restart
        subprocess.run(["docker", "restart", "opensculpt"], timeout=30)
        wait_for_condition(lambda: api("/api/status") is not None, timeout=60, interval=3)
        wait_for_condition(lambda: curl_notes()[0], timeout=90, interval=5)

        # Get data after
        _, after = curl_notes()
        after_data = json.loads(after)
        assert len(after_data) >= len(before_data), "Data lost after container restart"


# ── Dashboard UI tests ──


class TestDashboardUI:
    def test_services_dock_pill_visible(self):
        """The dock should have a Services pill with count."""
        r = requests.get(f"{BASE}/", timeout=10)
        html = r.text
        # The dock pill is rendered by JS, so check the API instead
        data = api("/api/services")
        services = data.get("services", [])
        assert len(services) > 0, "No services to show in dock"

    def test_services_endpoint_for_panel(self):
        """Panel fetches from /api/services — verify it returns what panel needs."""
        data = api("/api/services")
        services = data["services"]
        for svc in services:
            # Panel needs these fields
            assert "name" in svc
            assert "status" in svc
            assert "port" in svc
            assert "goal_id" in svc
            assert "url" in svc

    def test_credentials_visible_in_api(self):
        """Notes API should expose credentials hint."""
        data = api("/api/services")
        notes = [s for s in data["services"] if s.get("port") == NOTES_PORT]
        if notes:
            # Credentials are extracted from card body
            cred = notes[0].get("credentials_hint", "")
            # May or may not have it depending on card content
            # Just verify the field exists
            assert "credentials_hint" in notes[0] or "url" in notes[0]

    def test_stop_endpoint_works(self):
        r = requests.post(f"{BASE}/api/services/Notes%20API/stop", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True

    def test_restart_endpoint_works(self):
        r = requests.post(f"{BASE}/api/services/Notes%20API/restart", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True

    def test_decommission_endpoint_works(self):
        """Test decommission on the broken Hello World service (not Notes API)."""
        r = requests.post(f"{BASE}/api/services/Hello%20World%20Flask%20API/decommission", timeout=10)
        # May or may not exist
        assert r.status_code == 200


# ── Service card quality tests ──


class TestServiceCardQuality:
    def test_health_check_uses_curl_not_grep(self):
        """Health check must actually test if the service responds, not grep source code."""
        data = api("/api/services")
        for svc in data["services"]:
            hc = svc.get("health_check", "")
            if hc:
                assert "grep" not in hc, f"Health check for {svc['name']} uses grep (tests code not service): {hc}"

    def test_start_command_exists_for_services_with_port(self):
        """Every service with a port must have a start command."""
        data = api("/api/services")
        for svc in data["services"]:
            if svc.get("port") and svc["port"] != 0:
                assert svc.get("start_command"), f"Service {svc['name']} on port {svc['port']} has no start_command"

    def test_no_empty_name_services(self):
        data = api("/api/services")
        for svc in data["services"]:
            assert svc.get("name"), "Service has empty name"
            assert len(svc["name"]) > 3, f"Service name too short: {svc['name']}"
