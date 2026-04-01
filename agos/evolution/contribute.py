"""Community contribution — share evolution learnings via GitHub PR.

Uses the GitHub API to fork the main repo (if needed), create a branch,
commit evolved code + metadata, and open a pull request.

The PR includes:
  - community/contributions/{instance_id}.json  (metadata + archive)
  - community/evolved/{instance_id}/*.py        (actual evolved code)

This closes the federated learning loop: evolved code that passed sandbox
validation on one instance becomes available to all other instances.
"""

from __future__ import annotations

import json
import base64
from datetime import datetime

import httpx

from agos.config import settings

GITHUB_API = "https://api.github.com"


class ContributionError(Exception):
    """Failed to create a community contribution."""


async def share_learnings(
    contribution: dict,
    github_token: str,
    upstream_owner: str | None = None,
    upstream_repo: str | None = None,
) -> dict:
    """Create a GitHub PR with this instance's evolution learnings + code.

    The PR contains:
      1. Metadata JSON (strategies applied, fitness scores, archive)
      2. Evolved .py files that passed sandbox validation

    Returns {"pr_url": "...", "branch": "...", "files_committed": N}.
    Raises ContributionError on failure.
    """
    owner = upstream_owner or settings.github_owner
    repo = upstream_repo or settings.github_repo
    instance_id = contribution.get("instance_id", "unknown")[:12]
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch_name = f"contrib/{instance_id}/{timestamp}"

    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient(
        timeout=30, headers=headers, follow_redirects=True
    ) as client:
        # Step 1: Get authenticated user
        fork_user = await _get_authenticated_user(client)

        # Step 2: Fork the repo (idempotent)
        await _ensure_fork(client, owner, repo)

        # Step 3: Get default branch SHA from fork
        default_sha = await _get_default_branch_sha(client, fork_user, repo)

        # Step 4: Create branch on fork
        await _create_branch(client, fork_user, repo, branch_name, default_sha)

        # Step 5: Build file list for multi-file commit
        files: list[tuple[str, str]] = []  # (path, content)

        # 5a: Metadata JSON (strip evolved_code to avoid duplicating large content)
        meta = {k: v for k, v in contribution.items()
                if k != "evolved_code" and not k.startswith("_")}
        meta_json = json.dumps(meta, indent=2)
        files.append((
            f"community/contributions/{instance_id}.json",
            meta_json,
        ))

        # 5b: Evolved .py files
        evolved_code = contribution.get("evolved_code", {})
        for filename, code in evolved_code.items():
            files.append((
                f"community/evolved/{instance_id}/{filename}",
                code,
            ))

        # Step 6: Commit all files in one tree
        await _commit_tree(
            client, fork_user, repo, branch_name, default_sha,
            files=files,
            message=f"Add evolution learnings + {len(evolved_code)} evolved modules from {instance_id}",
        )

        # Step 7: Open PR against upstream
        n_strategies = len(contribution.get("strategies_applied", []))
        n_cycles = contribution.get("cycles_completed", 0)
        n_patterns = len(contribution.get("discovered_patterns", []))
        n_evolved = len(evolved_code)

        pr_title = (
            f"[Community] {n_evolved} evolved modules, "
            f"{n_strategies} strategies, {n_cycles} cycles"
        )
        pr_body = _build_pr_body(
            instance_id, n_cycles, n_strategies, n_patterns,
            n_evolved, contribution, evolved_code,
        )

        pr_url = await _create_pr(
            client, owner, repo,
            head=f"{fork_user}:{branch_name}",
            base="main",
            title=pr_title,
            body=pr_body,
        )

    return {
        "pr_url": pr_url,
        "branch": branch_name,
        "files_committed": len(files),
    }


def _build_pr_body(
    instance_id: str,
    n_cycles: int,
    n_strategies: int,
    n_patterns: int,
    n_evolved: int,
    contribution: dict,
    evolved_code: dict,
) -> str:
    """Build a markdown PR body with strategies + evolved code listing."""
    body = (
        f"## Community Evolution Contribution\n\n"
        f"- **Instance:** `{instance_id}`\n"
        f"- **Cycles completed:** {n_cycles}\n"
        f"- **Strategies applied:** {n_strategies}\n"
        f"- **Patterns discovered:** {n_patterns}\n"
        f"- **Evolved code files:** {n_evolved}\n\n"
    )

    if contribution.get("strategies_applied"):
        body += "### Strategies\n"
        for s in contribution["strategies_applied"]:
            sandbox = " (sandbox-passed)" if s.get("sandbox_passed") else ""
            body += (
                f"- **{s['name']}** (module: `{s['module']}`, "
                f"applied {s.get('applied_count', 1)}x){sandbox}\n"
            )
        body += "\n"

    if evolved_code:
        body += "### Evolved Code\n"
        body += "These files passed sandbox validation and are ready to use:\n\n"
        body += "| File | Path |\n|------|------|\n"
        for filename in sorted(evolved_code.keys()):
            body += (
                f"| `{filename}` | "
                f"`community/evolved/{instance_id}/{filename}` |\n"
            )
        body += "\n"

    archive = contribution.get("design_archive", {})
    if archive.get("entries"):
        body += f"### Design Archive ({len(archive['entries'])} designs)\n"
        for e in archive["entries"][:5]:
            body += (
                f"- **{e['strategy_name']}** "
                f"(module: `{e['module']}`, fitness: {e['current_fitness']:.2f}, "
                f"gen: {e['generation']})\n"
            )
        if len(archive["entries"]) > 5:
            body += f"- ... and {len(archive['entries']) - 5} more\n"
        body += "\n"

    body += "---\n*Auto-generated by AGenticOS evolution engine.*\n"
    return body


# ── GitHub API helpers ───────────────────────────────────────────


async def _get_authenticated_user(client: httpx.AsyncClient) -> str:
    resp = await client.get(f"{GITHUB_API}/user")
    if resp.status_code != 200:
        raise ContributionError(
            f"Auth failed ({resp.status_code}): check your GitHub token"
        )
    return resp.json()["login"]


async def _ensure_fork(
    client: httpx.AsyncClient, owner: str, repo: str
) -> None:
    resp = await client.post(f"{GITHUB_API}/repos/{owner}/{repo}/forks")
    if resp.status_code not in (200, 202):
        raise ContributionError(f"Fork failed: {resp.status_code}")


async def _get_default_branch_sha(
    client: httpx.AsyncClient, owner: str, repo: str
) -> str:
    resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}")
    if resp.status_code != 200:
        raise ContributionError(f"Repo not found: {resp.status_code}")
    default_branch = resp.json().get("default_branch", "main")
    ref = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{default_branch}"
    )
    if ref.status_code != 200:
        raise ContributionError(f"Branch ref failed: {ref.status_code}")
    return ref.json()["object"]["sha"]


async def _create_branch(
    client: httpx.AsyncClient, owner: str, repo: str,
    branch: str, sha: str,
) -> None:
    resp = await client.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )
    if resp.status_code not in (200, 201):
        raise ContributionError(f"Branch creation failed: {resp.status_code}")


async def _commit_tree(
    client: httpx.AsyncClient, owner: str, repo: str,
    branch: str, base_sha: str,
    files: list[tuple[str, str]],
    message: str,
) -> str:
    """Commit multiple files in a single Git tree operation.

    Uses the Git Data API (trees + commits) instead of Contents API
    so all files land in one atomic commit.

    Returns the new commit SHA.
    """
    # Step 1: Create blobs for each file
    tree_items = []
    for path, content in files:
        blob_resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs",
            json={
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "encoding": "base64",
            },
        )
        if blob_resp.status_code not in (200, 201):
            raise ContributionError(
                f"Blob creation failed for {path}: {blob_resp.status_code}"
            )
        blob_sha = blob_resp.json()["sha"]
        tree_items.append({
            "path": path,
            "mode": "100644",  # regular file
            "type": "blob",
            "sha": blob_sha,
        })

    # Step 2: Create tree with base_tree = parent commit's tree
    # First get the parent commit's tree SHA
    commit_resp = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/commits/{base_sha}"
    )
    if commit_resp.status_code != 200:
        raise ContributionError(f"Get commit failed: {commit_resp.status_code}")
    base_tree_sha = commit_resp.json()["tree"]["sha"]

    tree_resp = await client.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees",
        json={"base_tree": base_tree_sha, "tree": tree_items},
    )
    if tree_resp.status_code not in (200, 201):
        raise ContributionError(f"Tree creation failed: {tree_resp.status_code}")
    new_tree_sha = tree_resp.json()["sha"]

    # Step 3: Create commit pointing to the new tree
    new_commit_resp = await client.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/commits",
        json={
            "message": message,
            "tree": new_tree_sha,
            "parents": [base_sha],
        },
    )
    if new_commit_resp.status_code not in (200, 201):
        raise ContributionError(f"Commit creation failed: {new_commit_resp.status_code}")
    new_commit_sha = new_commit_resp.json()["sha"]

    # Step 4: Update branch ref to point to new commit
    ref_resp = await client.patch(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
        json={"sha": new_commit_sha},
    )
    if ref_resp.status_code != 200:
        raise ContributionError(f"Ref update failed: {ref_resp.status_code}")

    return new_commit_sha


# Keep _commit_file for backward compatibility (single-file commits)
async def _commit_file(
    client: httpx.AsyncClient, owner: str, repo: str,
    path: str, content_b64: str, branch: str, message: str,
) -> None:
    existing_sha = None
    check = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": branch},
    )
    if check.status_code == 200:
        existing_sha = check.json().get("sha")

    payload: dict = {
        "message": message,
        "content": content_b64,
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    resp = await client.put(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        json=payload,
    )
    if resp.status_code not in (200, 201):
        raise ContributionError(f"Commit failed: {resp.status_code}")


async def _create_pr(
    client: httpx.AsyncClient, owner: str, repo: str,
    head: str, base: str, title: str, body: str,
) -> str:
    resp = await client.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        json={"title": title, "body": body, "head": head, "base": base},
    )
    if resp.status_code not in (200, 201):
        raise ContributionError(f"PR creation failed: {resp.status_code}")
    return resp.json().get("html_url", "")
