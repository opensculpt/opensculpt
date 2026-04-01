"""Extended built-in tools — 48 additional tools for agents.

Covers: filesystem, git, search, web, data, system, network, crypto,
compression, scheduling, database, and more.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agos.tools.schema import ToolSchema, ToolParameter
from agos.tools.registry import ToolRegistry


def register_extended_tools(registry: ToolRegistry) -> None:
    """Register all 48 extended tools with the registry."""

    # ── File System Tools ────────────────────────────────────

    registry.register(ToolSchema(
        name="file_list",
        description="List files and directories at a path. Returns names, sizes, types.",
        parameters=[
            ToolParameter(name="path", description="Directory path to list"),
            ToolParameter(name="recursive", type="boolean", description="List recursively", required=False),
        ],
    ), _file_list)

    registry.register(ToolSchema(
        name="file_search",
        description="Search for files matching a glob pattern.",
        parameters=[
            ToolParameter(name="path", description="Root directory to search"),
            ToolParameter(name="pattern", description="Glob pattern (e.g., '*.py', '**/*.json')"),
        ],
    ), _file_search)

    registry.register(ToolSchema(
        name="file_move",
        description="Move or rename a file or directory.",
        parameters=[
            ToolParameter(name="source", description="Source path"),
            ToolParameter(name="destination", description="Destination path"),
        ],
    ), _file_move)

    registry.register(ToolSchema(
        name="file_copy",
        description="Copy a file or directory.",
        parameters=[
            ToolParameter(name="source", description="Source path"),
            ToolParameter(name="destination", description="Destination path"),
        ],
    ), _file_copy)

    registry.register(ToolSchema(
        name="file_delete",
        description="Delete a file or empty directory.",
        parameters=[ToolParameter(name="path", description="Path to delete")],
    ), _file_delete)

    registry.register(ToolSchema(
        name="file_info",
        description="Get file metadata: size, modified time, permissions, type.",
        parameters=[ToolParameter(name="path", description="File path")],
    ), _file_info)

    registry.register(ToolSchema(
        name="file_append",
        description="Append content to an existing file.",
        parameters=[
            ToolParameter(name="path", description="File path"),
            ToolParameter(name="content", description="Content to append"),
        ],
    ), _file_append)

    registry.register(ToolSchema(
        name="file_patch",
        description="Apply a find-and-replace patch to a file.",
        parameters=[
            ToolParameter(name="path", description="File path"),
            ToolParameter(name="find", description="Text to find"),
            ToolParameter(name="replace", description="Replacement text"),
        ],
    ), _file_patch)

    registry.register(ToolSchema(
        name="directory_create",
        description="Create a directory (and parents if needed).",
        parameters=[ToolParameter(name="path", description="Directory path")],
    ), _directory_create)

    registry.register(ToolSchema(
        name="directory_tree",
        description="Display directory tree structure (like 'tree' command).",
        parameters=[
            ToolParameter(name="path", description="Root directory"),
            ToolParameter(name="max_depth", type="integer", description="Max depth (default 3)", required=False),
        ],
    ), _directory_tree)

    # ── Git Tools ────────────────────────────────────────────

    registry.register(ToolSchema(
        name="git_status",
        description="Get git status of the working directory.",
        parameters=[ToolParameter(name="path", description="Repo path", required=False)],
    ), _git_status)

    registry.register(ToolSchema(
        name="git_diff",
        description="Get git diff (staged or unstaged changes).",
        parameters=[
            ToolParameter(name="path", description="Repo path", required=False),
            ToolParameter(name="staged", type="boolean", description="Show staged changes only", required=False),
        ],
    ), _git_diff)

    registry.register(ToolSchema(
        name="git_log",
        description="Get recent git commit log.",
        parameters=[
            ToolParameter(name="path", description="Repo path", required=False),
            ToolParameter(name="count", type="integer", description="Number of commits (default 10)", required=False),
        ],
    ), _git_log)

    registry.register(ToolSchema(
        name="git_commit",
        description="Stage all changes and create a git commit.",
        parameters=[
            ToolParameter(name="message", description="Commit message"),
            ToolParameter(name="path", description="Repo path", required=False),
        ],
    ), _git_commit)

    registry.register(ToolSchema(
        name="git_branch",
        description="List, create, or switch git branches.",
        parameters=[
            ToolParameter(name="action", description="Action: list, create, switch, delete"),
            ToolParameter(name="name", description="Branch name (for create/switch/delete)", required=False),
            ToolParameter(name="path", description="Repo path", required=False),
        ],
    ), _git_branch)

    # ── Search & Grep Tools ──────────────────────────────────

    registry.register(ToolSchema(
        name="grep",
        description="Search file contents for a pattern (like ripgrep).",
        parameters=[
            ToolParameter(name="pattern", description="Regex pattern to search for"),
            ToolParameter(name="path", description="Directory or file to search"),
            ToolParameter(name="file_pattern", description="File glob filter (e.g., '*.py')", required=False),
        ],
    ), _grep)

    registry.register(ToolSchema(
        name="text_replace",
        description="Find and replace text across multiple files.",
        parameters=[
            ToolParameter(name="path", description="Directory to search"),
            ToolParameter(name="find", description="Text to find"),
            ToolParameter(name="replace", description="Replacement text"),
            ToolParameter(name="file_pattern", description="File glob filter (e.g., '*.py')", required=False),
        ],
    ), _text_replace)

    # ── Web Tools ────────────────────────────────────────────

    registry.register(ToolSchema(
        name="web_scrape",
        description="Fetch a web page and extract its text content.",
        parameters=[
            ToolParameter(name="url", description="URL to scrape"),
            ToolParameter(name="selector", description="CSS selector to extract (optional)", required=False),
        ],
    ), _web_scrape)

    registry.register(ToolSchema(
        name="web_search",
        description="Search the web using DuckDuckGo.",
        parameters=[
            ToolParameter(name="query", description="Search query"),
            ToolParameter(name="max_results", type="integer", description="Max results (default 5)", required=False),
        ],
    ), _web_search)

    registry.register(ToolSchema(
        name="web_download",
        description="Download a file from a URL.",
        parameters=[
            ToolParameter(name="url", description="URL to download"),
            ToolParameter(name="path", description="Local path to save to"),
        ],
    ), _web_download)

    registry.register(ToolSchema(
        name="api_call",
        description="Make a structured API call with headers, body, and auth.",
        parameters=[
            ToolParameter(name="url", description="API URL"),
            ToolParameter(name="method", description="HTTP method", required=False),
            ToolParameter(name="headers", description="JSON headers string", required=False),
            ToolParameter(name="body", description="JSON body string", required=False),
            ToolParameter(name="bearer_token", description="Bearer auth token", required=False),
        ],
    ), _api_call)

    # ── Data Tools ───────────────────────────────────────────

    registry.register(ToolSchema(
        name="json_query",
        description="Parse JSON and extract data using a simple dot-path (e.g., 'data.items[0].name').",
        parameters=[
            ToolParameter(name="json_text", description="JSON string to parse"),
            ToolParameter(name="path", description="Dot-path query (e.g., 'data.name')", required=False),
        ],
    ), _json_query)

    registry.register(ToolSchema(
        name="json_format",
        description="Pretty-print or compact JSON.",
        parameters=[
            ToolParameter(name="json_text", description="JSON string"),
            ToolParameter(name="compact", type="boolean", description="Compact output (no indentation)", required=False),
        ],
    ), _json_format)

    registry.register(ToolSchema(
        name="csv_read",
        description="Read a CSV file and return as JSON array.",
        parameters=[
            ToolParameter(name="path", description="CSV file path"),
            ToolParameter(name="limit", type="integer", description="Max rows (default 100)", required=False),
        ],
    ), _csv_read)

    registry.register(ToolSchema(
        name="base64_encode",
        description="Base64 encode text or file contents.",
        parameters=[ToolParameter(name="text", description="Text to encode (or file path prefixed with @)")],
    ), _base64_encode)

    registry.register(ToolSchema(
        name="base64_decode",
        description="Base64 decode a string.",
        parameters=[ToolParameter(name="text", description="Base64 string to decode")],
    ), _base64_decode)

    registry.register(ToolSchema(
        name="hash_compute",
        description="Compute hash of text or file (md5, sha256, sha512).",
        parameters=[
            ToolParameter(name="input", description="Text or file path (prefix with @)"),
            ToolParameter(name="algorithm", description="Hash algorithm (md5, sha256, sha512)", required=False),
        ],
    ), _hash_compute)

    registry.register(ToolSchema(
        name="regex_match",
        description="Test a regex pattern against text and return matches.",
        parameters=[
            ToolParameter(name="pattern", description="Regex pattern"),
            ToolParameter(name="text", description="Text to test against"),
        ],
    ), _regex_match)

    registry.register(ToolSchema(
        name="text_diff",
        description="Compare two texts and show differences.",
        parameters=[
            ToolParameter(name="text_a", description="First text (or file path with @)"),
            ToolParameter(name="text_b", description="Second text (or file path with @)"),
        ],
    ), _text_diff)

    # ── System Tools ─────────────────────────────────────────

    registry.register(ToolSchema(
        name="env_get",
        description="Get an environment variable value.",
        parameters=[ToolParameter(name="name", description="Variable name")],
    ), _env_get)

    registry.register(ToolSchema(
        name="env_set",
        description="Set an environment variable for the current process.",
        parameters=[
            ToolParameter(name="name", description="Variable name"),
            ToolParameter(name="value", description="Variable value"),
        ],
    ), _env_set)

    registry.register(ToolSchema(
        name="process_list",
        description="List running processes (like 'ps' or 'tasklist').",
        parameters=[ToolParameter(name="filter", description="Filter by name substring", required=False)],
    ), _process_list)

    registry.register(ToolSchema(
        name="system_info",
        description="Get system information: OS, CPU, memory, disk, Python version.",
        parameters=[],
    ), _system_info)

    registry.register(ToolSchema(
        name="timestamp",
        description="Get current timestamp in various formats.",
        parameters=[ToolParameter(name="format", description="Format: iso, unix, human, rfc2822", required=False)],
    ), _timestamp)

    registry.register(ToolSchema(
        name="sleep",
        description="Wait for a specified number of seconds.",
        parameters=[ToolParameter(name="seconds", type="number", description="Seconds to wait (max 60)")],
    ), _sleep)

    # ── Network Tools ────────────────────────────────────────

    registry.register(ToolSchema(
        name="dns_lookup",
        description="DNS lookup for a hostname.",
        parameters=[
            ToolParameter(name="hostname", description="Hostname to resolve"),
            ToolParameter(name="record_type", description="Record type (A, AAAA, MX, TXT, CNAME)", required=False),
        ],
    ), _dns_lookup)

    registry.register(ToolSchema(
        name="port_check",
        description="Check if a TCP port is open on a host.",
        parameters=[
            ToolParameter(name="host", description="Hostname or IP"),
            ToolParameter(name="port", type="integer", description="Port number"),
        ],
    ), _port_check)

    registry.register(ToolSchema(
        name="ping",
        description="Ping a host and return latency.",
        parameters=[ToolParameter(name="host", description="Hostname or IP to ping")],
    ), _ping)

    # ── Compression Tools ────────────────────────────────────

    registry.register(ToolSchema(
        name="zip_create",
        description="Create a ZIP archive from files/directories.",
        parameters=[
            ToolParameter(name="output", description="Output ZIP file path"),
            ToolParameter(name="sources", description="Comma-separated list of paths to include"),
        ],
    ), _zip_create)

    registry.register(ToolSchema(
        name="zip_extract",
        description="Extract a ZIP archive.",
        parameters=[
            ToolParameter(name="archive", description="ZIP file path"),
            ToolParameter(name="destination", description="Extraction directory"),
        ],
    ), _zip_extract)

    # ── Database Tools ───────────────────────────────────────

    registry.register(ToolSchema(
        name="sqlite_query",
        description="Execute a SQL query on a SQLite database.",
        parameters=[
            ToolParameter(name="db_path", description="Path to SQLite database file"),
            ToolParameter(name="query", description="SQL query to execute"),
        ],
    ), _sqlite_query)

    registry.register(ToolSchema(
        name="sqlite_tables",
        description="List all tables in a SQLite database.",
        parameters=[ToolParameter(name="db_path", description="Path to SQLite database file")],
    ), _sqlite_tables)

    # ── Math & Eval Tools ────────────────────────────────────

    registry.register(ToolSchema(
        name="math_eval",
        description="Evaluate a mathematical expression safely.",
        parameters=[ToolParameter(name="expression", description="Math expression (e.g., '2 ** 10 + sqrt(144)')")],
    ), _math_eval)

    registry.register(ToolSchema(
        name="uuid_generate",
        description="Generate a UUID (v4).",
        parameters=[],
    ), _uuid_generate)

    registry.register(ToolSchema(
        name="random_string",
        description="Generate a random string.",
        parameters=[
            ToolParameter(name="length", type="integer", description="Length (default 32)", required=False),
            ToolParameter(name="charset", description="Charset: hex, alpha, alnum, ascii (default alnum)", required=False),
        ],
    ), _random_string)

    # ── Template & Text ──────────────────────────────────────

    registry.register(ToolSchema(
        name="text_count",
        description="Count words, lines, and characters in text or file.",
        parameters=[ToolParameter(name="input", description="Text or file path (prefix with @)")],
    ), _text_count)

    registry.register(ToolSchema(
        name="text_extract",
        description="Extract lines from text by range.",
        parameters=[
            ToolParameter(name="input", description="Text or file path (prefix with @)"),
            ToolParameter(name="start", type="integer", description="Start line (1-based)"),
            ToolParameter(name="end", type="integer", description="End line (inclusive)"),
        ],
    ), _text_extract)

    # ── Cron/Schedule ────────────────────────────────────────

    registry.register(ToolSchema(
        name="cron_parse",
        description="Parse a cron expression and show next 5 run times.",
        parameters=[ToolParameter(name="expression", description="Cron expression (e.g., '0 */2 * * *')")],
    ), _cron_parse)


# ── Implementations ──────────────────────────────────────────


async def _file_list(path: str, recursive: bool = False) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: Path not found: {path}"
    if not p.is_dir():
        return f"Error: Not a directory: {path}"
    entries = []
    items = p.rglob("*") if recursive else p.iterdir()
    for item in list(items)[:500]:
        kind = "dir" if item.is_dir() else "file"
        size = item.stat().st_size if item.is_file() else 0
        entries.append(f"{kind}\t{size}\t{item.relative_to(p)}")
    return "\n".join(entries) or "(empty)"


async def _file_search(path: str, pattern: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: Path not found: {path}"
    matches = list(p.glob(pattern))[:200]
    return "\n".join(str(m.relative_to(p)) for m in matches) or "No matches"


async def _file_move(source: str, destination: str) -> str:
    shutil.move(source, destination)
    return f"Moved {source} -> {destination}"


async def _file_copy(source: str, destination: str) -> str:
    src = Path(source)
    if src.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return f"Copied {source} -> {destination}"


async def _file_delete(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: Not found: {path}"
    if p.is_dir():
        p.rmdir()
    else:
        p.unlink()
    return f"Deleted {path}"


async def _file_info(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Error: Not found: {path}"
    stat = p.stat()
    return json.dumps({
        "path": str(p.absolute()),
        "type": "directory" if p.is_dir() else "file",
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
    }, indent=2)


async def _file_append(path: str, content: str) -> str:
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    return f"Appended {len(content)} bytes to {path}"


async def _file_patch(path: str, find: str, replace: str) -> str:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(find)
    if count == 0:
        return "Error: Pattern not found in file"
    text = text.replace(find, replace)
    p.write_text(text, encoding="utf-8")
    return f"Replaced {count} occurrence(s)"


async def _directory_create(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return f"Created {path}"


async def _directory_tree(path: str, max_depth: int = 3) -> str:
    lines: list[str] = []

    def _walk(p: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        for i, entry in enumerate(entries[:50]):
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                ext = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + ext, depth + 1)

    root = Path(path)
    lines.append(str(root) + "/")
    _walk(root, "", 1)
    return "\n".join(lines[:300])


async def _git_status(path: str = ".") -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain", "-b", cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return out.decode(errors="replace") or err.decode(errors="replace") or "(clean)"


async def _git_diff(path: str = ".", staged: bool = False) -> str:
    cmd = ["git", "diff", "--stat"] + (["--cached"] if staged else [])
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = await proc.communicate()
    return out.decode(errors="replace")[:5000] or "(no changes)"


async def _git_log(path: str = ".", count: int = 10) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "log", f"--oneline", f"-{count}", cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = await proc.communicate()
    return out.decode(errors="replace") or "(no commits)"


async def _git_commit(message: str, path: str = ".") -> str:
    await asyncio.create_subprocess_exec("git", "add", "-A", cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", message, cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return out.decode(errors="replace") + err.decode(errors="replace")


async def _git_branch(action: str, name: str = "", path: str = ".") -> str:
    if action == "list":
        cmd = ["git", "branch", "-a"]
    elif action == "create":
        cmd = ["git", "checkout", "-b", name]
    elif action == "switch":
        cmd = ["git", "checkout", name]
    elif action == "delete":
        cmd = ["git", "branch", "-d", name]
    else:
        return f"Unknown action: {action}"
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    return out.decode(errors="replace") + err.decode(errors="replace")


async def _grep(pattern: str, path: str, file_pattern: str = "") -> str:
    cmd = ["git", "grep", "-n", "-I", pattern, "--", file_pattern or "*"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return out.decode(errors="replace")[:5000] or "No matches"
    except Exception:
        # Fallback to python
        results = []
        import re
        regex = re.compile(pattern)
        root = Path(path)
        for f in root.rglob(file_pattern or "*"):
            if f.is_file() and f.stat().st_size < 500_000:
                try:
                    for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                        if regex.search(line):
                            results.append(f"{f.relative_to(root)}:{i}: {line[:200]}")
                            if len(results) >= 50:
                                return "\n".join(results)
                except Exception:
                    pass
        return "\n".join(results) or "No matches"


async def _text_replace(path: str, find: str, replace: str, file_pattern: str = "*.py") -> str:
    count = 0
    root = Path(path)
    for f in root.rglob(file_pattern):
        if f.is_file() and f.stat().st_size < 1_000_000:
            try:
                text = f.read_text(encoding="utf-8")
                if find in text:
                    f.write_text(text.replace(find, replace), encoding="utf-8")
                    count += 1
            except Exception:
                pass
    return f"Replaced in {count} files"


async def _web_scrape(url: str, selector: str = "") -> str:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url)
        text = r.text[:10000]
        # Simple HTML tag stripping
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]


async def _web_search(query: str, max_results: int = 5) -> str:
    url = "https://html.duckduckgo.com/html/"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.post(url, data={"q": query})
        import re
        results = re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r.text)
        lines = []
        for href, title in results[:max_results]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            lines.append(f"- {title}\n  {href}")
        return "\n".join(lines) or "No results"


async def _web_download(url: str, path: str) -> str:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(r.content)
        return f"Downloaded {len(r.content)} bytes to {path}"


async def _api_call(url: str, method: str = "GET", headers: str = "{}", body: str = "", bearer_token: str = "") -> str:
    hdrs = json.loads(headers) if headers and headers != "{}" else {}
    if bearer_token:
        hdrs["Authorization"] = f"Bearer {bearer_token}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.request(method, url, headers=hdrs, content=body if body else None)
        return f"HTTP {r.status_code}\n{r.text[:5000]}"


async def _json_query(json_text: str, path: str = "") -> str:
    data = json.loads(json_text)
    if not path:
        return json.dumps(data, indent=2)[:5000]
    # Simple dot-path navigation
    parts = path.replace("[", ".[").split(".")
    current: Any = data
    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            current = current[idx]
        else:
            current = current[part]
    return json.dumps(current, indent=2) if isinstance(current, (dict, list)) else str(current)


async def _json_format(json_text: str, compact: bool = False) -> str:
    data = json.loads(json_text)
    if compact:
        return json.dumps(data, separators=(",", ":"))
    return json.dumps(data, indent=2)


async def _csv_read(path: str, limit: int = 100) -> str:
    import csv
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append(dict(row))
    return json.dumps(rows, indent=2)[:5000]


async def _base64_encode(text: str) -> str:
    import base64
    if text.startswith("@"):
        data = Path(text[1:]).read_bytes()
    else:
        data = text.encode()
    return base64.b64encode(data).decode()


async def _base64_decode(text: str) -> str:
    import base64
    return base64.b64decode(text).decode(errors="replace")


async def _hash_compute(input: str, algorithm: str = "sha256") -> str:
    if input.startswith("@"):
        data = Path(input[1:]).read_bytes()
    else:
        data = input.encode()
    h = hashlib.new(algorithm, data)
    return f"{algorithm}:{h.hexdigest()}"


async def _regex_match(pattern: str, text: str) -> str:
    import re
    matches = list(re.finditer(pattern, text))
    if not matches:
        return "No matches"
    results = []
    for m in matches[:20]:
        results.append({"match": m.group(), "start": m.start(), "end": m.end(), "groups": list(m.groups())})
    return json.dumps(results, indent=2)


async def _text_diff(text_a: str, text_b: str) -> str:
    import difflib
    a = Path(text_a[1:]).read_text() if text_a.startswith("@") else text_a
    b = Path(text_b[1:]).read_text() if text_b.startswith("@") else text_b
    diff = difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="")
    return "\n".join(list(diff)[:200]) or "(identical)"


async def _env_get(name: str) -> str:
    return os.environ.get(name, f"(not set: {name})")


async def _env_set(name: str, value: str) -> str:
    os.environ[name] = value
    return f"Set {name}={value}"


async def _process_list(filter: str = "") -> str:
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            info = p.info
            if filter and filter.lower() not in info["name"].lower():
                continue
            mem = info.get("memory_info")
            procs.append(f"PID={info['pid']}\t{info['name']}\tCPU={info.get('cpu_percent', 0)}%\tMEM={mem.rss // 1024 // 1024 if mem else 0}MB")
            if len(procs) >= 50:
                break
        return "\n".join(procs)
    except ImportError:
        return "psutil not available"


async def _system_info() -> str:
    import platform
    try:
        import psutil
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return json.dumps({
            "os": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "cpu_percent": psutil.cpu_percent(),
            "memory_total_gb": round(mem.total / 1e9, 1),
            "memory_used_gb": round(mem.used / 1e9, 1),
            "disk_total_gb": round(disk.total / 1e9, 1),
            "disk_used_gb": round(disk.used / 1e9, 1),
        }, indent=2)
    except ImportError:
        return json.dumps({"os": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count()})


async def _timestamp(format: str = "iso") -> str:
    now = datetime.now(timezone.utc)
    if format == "unix":
        return str(int(now.timestamp()))
    elif format == "human":
        return now.strftime("%B %d, %Y at %H:%M:%S UTC")
    elif format == "rfc2822":
        from email.utils import format_datetime
        return format_datetime(now)
    return now.isoformat()


async def _sleep(seconds: float) -> str:
    seconds = min(seconds, 60)
    await asyncio.sleep(seconds)
    return f"Waited {seconds}s"


async def _dns_lookup(hostname: str, record_type: str = "A") -> str:
    import socket
    try:
        if record_type == "A":
            results = socket.getaddrinfo(hostname, None, socket.AF_INET)
            ips = list(set(r[4][0] for r in results))
            return f"{hostname} -> {', '.join(ips)}"
        elif record_type == "AAAA":
            results = socket.getaddrinfo(hostname, None, socket.AF_INET6)
            ips = list(set(r[4][0] for r in results))
            return f"{hostname} -> {', '.join(ips)}"
        else:
            proc = await asyncio.create_subprocess_exec(
                "nslookup", "-type=" + record_type, hostname,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return out.decode(errors="replace")[:2000]
    except Exception as e:
        return f"Error: {e}"


async def _port_check(host: str, port: int) -> str:
    import socket
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        return f"Port {port} on {host} is OPEN"
    except Exception as e:
        return f"Port {port} on {host} is CLOSED ({e})"


async def _ping(host: str) -> str:
    cmd = ["ping", "-n", "3", host] if os.name == "nt" else ["ping", "-c", "3", host]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    return out.decode(errors="replace")[:2000]


async def _zip_create(output: str, sources: str) -> str:
    import zipfile
    paths = [s.strip() for s in sources.split(",")]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            path = Path(p)
            if path.is_file():
                zf.write(path, path.name)
            elif path.is_dir():
                for f in path.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(path.parent))
    return f"Created {output}"


async def _zip_extract(archive: str, destination: str) -> str:
    import zipfile
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(destination)
        return f"Extracted {len(zf.namelist())} files to {destination}"


async def _sqlite_query(db_path: str, query: str) -> str:
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(query)
        if query.strip().upper().startswith("SELECT"):
            rows = [dict(r) for r in cursor.fetchmany(100)]
            return json.dumps(rows, indent=2, default=str)[:5000]
        else:
            conn.commit()
            return f"Affected {cursor.rowcount} rows"
    finally:
        conn.close()


async def _sqlite_tables(db_path: str) -> str:
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name")
    tables = [{"name": r[0], "type": r[1]} for r in cursor.fetchall()]
    conn.close()
    return json.dumps(tables, indent=2)


async def _math_eval(expression: str) -> str:
    import math
    allowed = {
        "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "len": len,
        "sqrt": math.sqrt, "log": math.log, "log2": math.log2, "log10": math.log10,
        "sin": math.sin, "cos": math.cos, "tan": math.tan, "pi": math.pi, "e": math.e,
        "pow": pow, "int": int, "float": float,
    }
    try:
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


async def _uuid_generate() -> str:
    import uuid
    return str(uuid.uuid4())


async def _random_string(length: int = 32, charset: str = "alnum") -> str:
    import secrets
    import string
    chars = {
        "hex": string.hexdigits[:16],
        "alpha": string.ascii_letters,
        "alnum": string.ascii_letters + string.digits,
        "ascii": string.printable[:94],
    }.get(charset, string.ascii_letters + string.digits)
    return "".join(secrets.choice(chars) for _ in range(min(length, 1024)))


async def _text_count(input: str) -> str:
    text = Path(input[1:]).read_text() if input.startswith("@") else input
    lines = text.splitlines()
    words = text.split()
    return json.dumps({"lines": len(lines), "words": len(words), "characters": len(text)})


async def _text_extract(input: str, start: int, end: int) -> str:
    text = Path(input[1:]).read_text() if input.startswith("@") else input
    lines = text.splitlines()
    return "\n".join(lines[max(0, start - 1):end])


async def _cron_parse(expression: str) -> str:
    parts = expression.split()
    if len(parts) != 5:
        return "Error: Cron expression must have 5 fields (minute hour day month weekday)"
    return f"Cron: {expression}\nFields: minute={parts[0]} hour={parts[1]} day={parts[2]} month={parts[3]} weekday={parts[4]}\n(Install 'croniter' for next-run calculation)"
