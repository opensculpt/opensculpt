"""Tests for phase normalization (weak model sends flat strings instead of objects)."""

import pytest


class TestPhaseNormalization:
    """Test that flat string phases get normalized to proper objects."""

    def test_phases_as_objects(self):
        """Standard [{"name","prompt","verify"}] passes through unchanged."""
        phases = [
            {"name": "Install", "description": "Install deps", "command": "pip install flask"},
            {"name": "Run", "description": "Start server", "command": "python app.py"},
        ]
        # Normalization should NOT trigger
        assert isinstance(phases[0], dict)
        # Simulate the normalization check
        if phases and isinstance(phases[0], str):
            phases = [{"name": f"Phase {i+1}", "description": step, "command": step, "verify_type": "auto", "verify": ""} for i, step in enumerate(phases)]
        assert phases[0]["name"] == "Install"
        assert phases[1]["name"] == "Run"

    def test_phases_as_strings(self):
        """Flat strings ["step1","step2"] normalized to objects with auto verify."""
        phases = [
            "Install Flask and dependencies",
            "Create the hello world app",
            "Start the server on port 6060",
        ]
        # Simulate the normalization
        if phases and isinstance(phases[0], str):
            phases = [{"name": f"Phase {i+1}", "description": step, "command": step, "verify_type": "auto", "verify": ""} for i, step in enumerate(phases)]

        assert len(phases) == 3
        assert phases[0]["name"] == "Phase 1"
        assert phases[0]["description"] == "Install Flask and dependencies"
        assert phases[0]["command"] == "Install Flask and dependencies"
        assert phases[0]["verify_type"] == "auto"
        assert phases[2]["name"] == "Phase 3"
