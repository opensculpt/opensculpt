"""Self-update system for agos.

Checks PyPI for newer versions and can self-update via pip.
Also checks GitHub Releases for .exe updates on Windows.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from typing import Any

import httpx
from packaging.version import Version

from agos import __version__

PYPI_URL = "https://pypi.org/pypi/agos/json"
GITHUB_RELEASES_URL = "https://api.github.com/repos/{owner}/{repo}/releases/latest"

# Hardcoded: updates ONLY come from the official repo. No env var override.
UPSTREAM_OWNER = "opensculpt"
UPSTREAM_REPO = "opensculpt"


async def check_for_update() -> dict[str, Any]:
    """Check if a newer version is available.

    Checks PyPI first, then falls back to GitHub Releases.
    Update source is hardcoded to opensculpt/opensculpt — not configurable.
    Returns dict with current_version, latest_version, update_available,
    source, and download_url.
    """
    current = Version(__version__)
    result: dict[str, Any] = {
        "current_version": str(current),
        "latest_version": str(current),
        "update_available": False,
        "source": "",
        "download_url": "",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # Check PyPI first
        try:
            resp = await client.get(PYPI_URL)
            if resp.status_code == 200:
                data = resp.json()
                latest_str = data["info"]["version"]
                latest = Version(latest_str)
                if latest > current:
                    result["latest_version"] = str(latest)
                    result["update_available"] = True
                    result["source"] = "pypi"
                    return result
        except Exception:
            pass

        # Fallback: check GitHub releases
        try:
            url = GITHUB_RELEASES_URL.format(owner=UPSTREAM_OWNER, repo=UPSTREAM_REPO)
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "").lstrip("v")
                if tag:
                    latest = Version(tag)
                    if latest > current:
                        result["latest_version"] = str(latest)
                        result["update_available"] = True
                        result["source"] = "github"
                        # Find .exe asset for Windows
                        if platform.system() == "Windows":
                            for asset in data.get("assets", []):
                                if asset["name"].endswith(".exe"):
                                    result["download_url"] = asset[
                                        "browser_download_url"
                                    ]
                                    break
        except Exception:
            pass

    return result


def self_update() -> bool:
    """Update opensculpt via pip. Returns True on success."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "opensculpt"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False
