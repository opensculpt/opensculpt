"""Tests for release management."""

import pytest
import tempfile
from pathlib import Path

from agos.release import (
    BumpType,
    get_current_version,
    bump_version,
    update_version_in_pyproject,
    generate_changelog_entry,
    prepend_to_changelog,
)


# ── bump_version tests ──────────────────────────────────────────

def test_bump_patch():
    assert bump_version("0.1.0", BumpType.PATCH) == "0.1.1"


def test_bump_minor():
    assert bump_version("0.1.3", BumpType.MINOR) == "0.2.0"


def test_bump_major():
    assert bump_version("1.2.3", BumpType.MAJOR) == "2.0.0"


def test_bump_from_zero():
    assert bump_version("0.0.0", BumpType.PATCH) == "0.0.1"


def test_bump_invalid_format():
    with pytest.raises(ValueError):
        bump_version("1.0", BumpType.PATCH)


# ── get_current_version / update_version tests ─────────────────

def test_get_current_version():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write('[project]\nname = "agos"\nversion = "1.2.3"\n')
        f.flush()
        path = Path(f.name)

    try:
        assert get_current_version(path) == "1.2.3"
    finally:
        path.unlink()


def test_get_version_not_found():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write('[project]\nname = "agos"\n')
        f.flush()
        path = Path(f.name)

    try:
        with pytest.raises(ValueError):
            get_current_version(path)
    finally:
        path.unlink()


def test_update_version():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write('[project]\nname = "agos"\nversion = "0.1.0"\n')
        f.flush()
        path = Path(f.name)

    try:
        update_version_in_pyproject("0.2.0", path)
        assert get_current_version(path) == "0.2.0"
    finally:
        path.unlink()


# ── generate_changelog_entry tests ──────────────────────────────

def test_changelog_entry_basic():
    entry = generate_changelog_entry("0.2.0")
    assert "[0.2.0]" in entry


def test_changelog_entry_with_git_log():
    entry = generate_changelog_entry("0.2.0", git_log="Added feature X\nFixed bug Y")
    assert "Added feature X" in entry
    assert "Fixed bug Y" in entry
    assert "### Changes" in entry


def test_changelog_entry_with_proposals():
    proposals = [
        {"technique": "softmax scoring", "module": "knowledge", "description": "Better retrieval"},
    ]
    entry = generate_changelog_entry("0.2.0", evolution_proposals=proposals)
    assert "softmax scoring" in entry
    assert "Evolution Proposals" in entry


# ── prepend_to_changelog tests ──────────────────────────────────

def test_prepend_to_new_changelog(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    prepend_to_changelog("## [0.1.0] -- 2025-01-01\n\nInitial.", path)
    content = path.read_text()
    assert "# Changelog" in content
    assert "[0.1.0]" in content


def test_prepend_to_existing_changelog(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [0.1.0] -- old\n\nOld stuff.\n")

    prepend_to_changelog("## [0.2.0] -- new\n\nNew stuff.", path)
    content = path.read_text()
    assert content.index("[0.2.0]") < content.index("[0.1.0]")
