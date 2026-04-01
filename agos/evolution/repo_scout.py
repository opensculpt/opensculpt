"""RepoScout — discovers and fetches code from research paper repositories.

Given a paper, finds its GitHub repo (from abstract, URLs, or by searching),
then fetches key source files via the GitHub API. No git clone needed.
"""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, Field

from agos.types import new_id

GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

# File extensions we care about
CODE_EXTENSIONS = {".py", ".js", ".ts", ".rs", ".go", ".java"}
# Files to always try to fetch
PRIORITY_FILES = {"README.md", "readme.md", "README.rst"}
# Max file size to fetch (100KB)
MAX_FILE_SIZE = 100_000


class RepoFile(BaseModel):
    """A single file fetched from a repository."""

    path: str = ""
    content: str = ""
    size: int = 0
    language: str = ""


class RepoSnapshot(BaseModel):
    """A snapshot of code from a research repository."""

    id: str = Field(default_factory=new_id)
    repo_url: str = ""
    owner: str = ""
    repo_name: str = ""
    description: str = ""
    stars: int = 0
    language: str = ""
    files: list[RepoFile] = Field(default_factory=list)
    readme: str = ""
    file_tree: list[str] = Field(default_factory=list)

    @property
    def total_code_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def code_files(self) -> list[RepoFile]:
        return [f for f in self.files if f.path.endswith(".py")]


class RepoScout:
    """Discovers GitHub repos from papers and fetches their code."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def find_repo(self, paper_abstract: str, paper_title: str = "") -> str | None:
        """Extract or search for a GitHub repo URL from a paper."""
        # Strategy 1: Direct URL extraction from abstract
        urls = self._extract_github_urls(paper_abstract)
        if urls:
            return urls[0]

        # Strategy 2: Search GitHub by paper title
        repo_url = await self._search_github(paper_title)
        return repo_url

    async def fetch_repo(
        self, repo_url: str, max_files: int = 15
    ) -> RepoSnapshot | None:
        """Fetch key files from a GitHub repository via the API."""
        owner, repo = self._parse_repo_url(repo_url)
        if not owner or not repo:
            return None

        snapshot = RepoSnapshot(
            repo_url=repo_url,
            owner=owner,
            repo_name=repo,
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # Fetch repo metadata
            try:
                meta = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}")
                if meta.status_code == 200:
                    data = meta.json()
                    snapshot.description = data.get("description", "") or ""
                    snapshot.stars = data.get("stargazers_count", 0)
                    snapshot.language = data.get("language", "") or ""
            except Exception:
                pass

            # Fetch file tree (recursive)
            try:
                tree_resp = await client.get(
                    f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/main",
                    params={"recursive": "1"},
                )
                if tree_resp.status_code == 404:
                    # Try 'master' branch
                    tree_resp = await client.get(
                        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/master",
                        params={"recursive": "1"},
                    )

                if tree_resp.status_code == 200:
                    tree_data = tree_resp.json()
                    items = tree_data.get("tree", [])
                    snapshot.file_tree = [
                        item["path"] for item in items
                        if item.get("type") == "blob"
                    ]
            except Exception:
                pass

            # Select which files to fetch
            files_to_fetch = self._select_files(snapshot.file_tree, max_files)

            # Fetch each file
            branch = "main"
            for file_path in files_to_fetch:
                try:
                    raw_url = f"{GITHUB_RAW}/{owner}/{repo}/{branch}/{file_path}"
                    resp = await client.get(raw_url)
                    if resp.status_code == 404:
                        raw_url = f"{GITHUB_RAW}/{owner}/{repo}/master/{file_path}"
                        resp = await client.get(raw_url)

                    if resp.status_code == 200 and len(resp.text) <= MAX_FILE_SIZE:
                        ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
                        lang = _ext_to_lang(ext)

                        rf = RepoFile(
                            path=file_path,
                            content=resp.text,
                            size=len(resp.text),
                            language=lang,
                        )
                        if file_path.lower() in ("readme.md", "readme.rst", "readme.txt"):
                            snapshot.readme = resp.text[:5000]
                        else:
                            snapshot.files.append(rf)
                except Exception:
                    continue

        return snapshot

    def _extract_github_urls(self, text: str) -> list[str]:
        """Extract GitHub repository URLs from text."""
        pattern = r'https?://github\.com/[\w\-\.]+/[\w\-\.]+'
        urls = re.findall(pattern, text)
        # Deduplicate and clean
        cleaned = []
        seen = set()
        for url in urls:
            # Remove trailing punctuation
            url = url.rstrip(".,;:)")
            # Normalize
            url = url.split("#")[0].split("?")[0].rstrip("/")
            if url not in seen:
                seen.add(url)
                cleaned.append(url)
        return cleaned

    async def _search_github(self, query: str) -> str | None:
        """Search GitHub for a repo matching a paper title."""
        if not query:
            return None

        # Clean the query for GitHub search
        clean = re.sub(r'[^\w\s]', '', query)[:80]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{GITHUB_API}/search/repositories",
                    params={"q": clean, "sort": "stars", "per_page": "3"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        return items[0].get("html_url", "")
        except Exception:
            pass
        return None

    def _parse_repo_url(self, url: str) -> tuple[str, str]:
        """Extract owner and repo name from a GitHub URL."""
        url = url.rstrip("/")
        match = re.match(r'https?://github\.com/([\w\-\.]+)/([\w\-\.]+)', url)
        if match:
            return match.group(1), match.group(2)
        return "", ""

    def _select_files(self, file_tree: list[str], max_files: int) -> list[str]:
        """Select the most important files to fetch."""
        selected = []

        # Always get README
        for f in file_tree:
            if f.lower() in ("readme.md", "readme.rst", "readme.txt"):
                selected.append(f)
                break

        # Get Python files, prioritizing core/src directories and key patterns
        py_files = [f for f in file_tree if f.endswith(".py")]

        # Priority scoring
        scored = []
        for f in py_files:
            score = 0
            fl = f.lower()
            # Core implementation files
            if any(p in fl for p in ("core/", "src/", "lib/")):
                score += 3
            # Files with key names
            if any(p in fl for p in ("agent", "memory", "memo", "main", "engine", "model")):
                score += 2
            # Avoid tests, configs, setup
            if any(p in fl for p in ("test", "setup", "config", "__init__")):
                score -= 2
            if any(p in fl for p in ("docker", "deploy", "ci/")):
                score -= 3
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        for _, f in scored:
            if len(selected) >= max_files:
                break
            if f not in selected:
                selected.append(f)

        return selected


def _ext_to_lang(ext: str) -> str:
    """Map file extension to language name."""
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".md": "markdown",
        ".sh": "shell",
    }
    return mapping.get(ext, "")
