"""Tests for the auto-update system."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from agos.updater import check_for_update, self_update


# ── check_for_update tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_check_no_update_available():
    """When PyPI returns the same version, no update available."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"info": {"version": "0.1.0"}}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agos.updater.httpx.AsyncClient", return_value=mock_client):
        with patch("agos.updater.__version__", "0.1.0"):
            result = await check_for_update()

    assert not result["update_available"]
    assert result["current_version"] == "0.1.0"


@pytest.mark.asyncio
async def test_check_update_available_pypi():
    """When PyPI has a newer version, returns update available."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"info": {"version": "0.2.0"}}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agos.updater.httpx.AsyncClient", return_value=mock_client):
        with patch("agos.updater.__version__", "0.1.0"):
            result = await check_for_update()

    assert result["update_available"]
    assert result["latest_version"] == "0.2.0"
    assert result["source"] == "pypi"


@pytest.mark.asyncio
async def test_check_pypi_fails_fallback_github():
    """When PyPI fails, falls back to GitHub Releases."""
    pypi_resp = MagicMock()
    pypi_resp.status_code = 500

    github_resp = MagicMock()
    github_resp.status_code = 200
    github_resp.json.return_value = {
        "tag_name": "v0.3.0",
        "assets": [],
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[pypi_resp, github_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agos.updater.httpx.AsyncClient", return_value=mock_client):
        with patch("agos.updater.__version__", "0.1.0"):
            result = await check_for_update()

    assert result["update_available"]
    assert result["latest_version"] == "0.3.0"
    assert result["source"] == "github"


@pytest.mark.asyncio
async def test_check_both_fail():
    """When both PyPI and GitHub fail, no update reported."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agos.updater.httpx.AsyncClient", return_value=mock_client):
        with patch("agos.updater.__version__", "0.1.0"):
            result = await check_for_update()

    assert not result["update_available"]


@pytest.mark.asyncio
async def test_check_network_error():
    """Network errors are handled gracefully."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agos.updater.httpx.AsyncClient", return_value=mock_client):
        with patch("agos.updater.__version__", "0.1.0"):
            result = await check_for_update()

    assert not result["update_available"]


@pytest.mark.asyncio
async def test_check_github_exe_asset():
    """Windows .exe download URL is extracted from GitHub assets."""
    pypi_resp = MagicMock()
    pypi_resp.status_code = 404

    github_resp = MagicMock()
    github_resp.status_code = 200
    github_resp.json.return_value = {
        "tag_name": "v0.2.0",
        "assets": [
            {
                "name": "agos.exe",
                "browser_download_url": "https://github.com/opensculpt/opensculpt/releases/download/v0.2.0/opensculpt.exe",
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[pypi_resp, github_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agos.updater.httpx.AsyncClient", return_value=mock_client):
        with patch("agos.updater.__version__", "0.1.0"):
            with patch("agos.updater.platform.system", return_value="Windows"):
                result = await check_for_update()

    assert result["update_available"]
    assert "agos.exe" in result["download_url"]


# ── self_update tests ───────────────────────────────────────────

def test_self_update_success():
    """Successful pip update returns True."""
    with patch("agos.updater.subprocess.check_call"):
        assert self_update() is True


def test_self_update_failure():
    """Failed pip update returns False."""
    import subprocess
    with patch("agos.updater.subprocess.check_call", side_effect=subprocess.CalledProcessError(1, "pip")):
        assert self_update() is False
