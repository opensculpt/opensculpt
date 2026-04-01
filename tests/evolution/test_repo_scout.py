"""Tests for the RepoScout."""

import pytest

from agos.evolution.repo_scout import (
    RepoScout, RepoFile, RepoSnapshot, _ext_to_lang,
    CODE_EXTENSIONS,
)


def test_repo_file_model():
    rf = RepoFile(path="src/main.py", content="print('hi')", size=11, language="python")
    assert rf.path == "src/main.py"
    assert rf.size == 11
    assert rf.language == "python"


def test_repo_snapshot_model():
    snap = RepoSnapshot(
        repo_url="https://github.com/test/repo",
        owner="test",
        repo_name="repo",
        description="A test repo",
        stars=42,
        language="Python",
    )
    assert snap.owner == "test"
    assert snap.repo_name == "repo"
    assert snap.stars == 42
    assert snap.total_code_size == 0
    assert snap.code_files == []
    assert snap.id  # auto-generated


def test_repo_snapshot_code_files():
    snap = RepoSnapshot(
        repo_url="https://github.com/test/repo",
        owner="test",
        repo_name="repo",
        files=[
            RepoFile(path="main.py", content="x", size=1, language="python"),
            RepoFile(path="utils.js", content="y", size=1, language="javascript"),
            RepoFile(path="core.py", content="zz", size=2, language="python"),
        ],
    )
    assert snap.total_code_size == 4
    assert len(snap.code_files) == 2  # only .py files


def test_extract_github_urls():
    scout = RepoScout()
    text = "Our code is at https://github.com/zksha/alma and also https://github.com/user/repo2."
    urls = scout._extract_github_urls(text)
    assert len(urls) == 2
    assert "https://github.com/zksha/alma" in urls
    assert "https://github.com/user/repo2" in urls


def test_extract_github_urls_deduplicates():
    scout = RepoScout()
    text = "See https://github.com/a/b and https://github.com/a/b again."
    urls = scout._extract_github_urls(text)
    assert len(urls) == 1


def test_extract_github_urls_strips_trailing():
    scout = RepoScout()
    text = "Code: https://github.com/a/b, more at https://github.com/c/d."
    urls = scout._extract_github_urls(text)
    assert urls[0] == "https://github.com/a/b"
    assert urls[1] == "https://github.com/c/d"


def test_extract_github_urls_strips_fragments():
    scout = RepoScout()
    text = "https://github.com/a/b#readme and https://github.com/c/d?tab=about"
    urls = scout._extract_github_urls(text)
    assert urls[0] == "https://github.com/a/b"
    assert urls[1] == "https://github.com/c/d"


def test_extract_github_urls_empty():
    scout = RepoScout()
    urls = scout._extract_github_urls("No URLs here at all.")
    assert urls == []


def test_parse_repo_url():
    scout = RepoScout()
    owner, repo = scout._parse_repo_url("https://github.com/zksha/alma")
    assert owner == "zksha"
    assert repo == "alma"


def test_parse_repo_url_trailing_slash():
    scout = RepoScout()
    owner, repo = scout._parse_repo_url("https://github.com/user/project/")
    assert owner == "user"
    assert repo == "project"


def test_parse_repo_url_invalid():
    scout = RepoScout()
    owner, repo = scout._parse_repo_url("https://example.com/not/github")
    assert owner == ""
    assert repo == ""


def test_select_files_priority():
    scout = RepoScout()
    file_tree = [
        "README.md",
        "setup.py",
        "tests/test_main.py",
        "src/agent.py",
        "src/memory.py",
        "config.yaml",
        "__init__.py",
        "docs/guide.md",
        "src/engine.py",
        "core/model.py",
    ]
    selected = scout._select_files(file_tree, max_files=5)

    # README should always be first
    assert selected[0] == "README.md"
    # Core/src files with key names should be prioritized
    assert "src/agent.py" in selected
    assert "src/memory.py" in selected
    assert "src/engine.py" in selected or "core/model.py" in selected
    # Tests and setup should be deprioritized
    assert len(selected) == 5


def test_select_files_empty():
    scout = RepoScout()
    selected = scout._select_files([], max_files=10)
    assert selected == []


def test_select_files_max_limit():
    scout = RepoScout()
    file_tree = [f"file_{i}.py" for i in range(30)]
    selected = scout._select_files(file_tree, max_files=5)
    assert len(selected) <= 5


def test_ext_to_lang():
    assert _ext_to_lang(".py") == "python"
    assert _ext_to_lang(".js") == "javascript"
    assert _ext_to_lang(".ts") == "typescript"
    assert _ext_to_lang(".rs") == "rust"
    assert _ext_to_lang(".go") == "go"
    assert _ext_to_lang(".java") == "java"
    assert _ext_to_lang(".xyz") == ""


@pytest.mark.asyncio
async def test_find_repo_extracts_url():
    scout = RepoScout()
    url = await scout.find_repo("Code at https://github.com/zksha/alma for details.")
    assert url == "https://github.com/zksha/alma"


def test_code_extensions():
    assert ".py" in CODE_EXTENSIONS
    assert ".js" in CODE_EXTENSIONS
    assert ".rs" in CODE_EXTENSIONS
