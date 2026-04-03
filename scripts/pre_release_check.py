#!/usr/bin/env python3
"""Pre-release security check — run before publishing to PyPI or building .exe.

Usage:
    python scripts/pre_release_check.py          # check everything
    python scripts/pre_release_check.py --fix    # auto-fix what it can

Exit code 0 = safe to release, 1 = blocked.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAIL = False

# ── Patterns that should NEVER appear in distributable files ──
SECRET_PATTERNS = [
    (r'sk-or-v1-[a-zA-Z0-9]{50,}', "OpenRouter API key"),
    (r'sk-ant-api[0-9]+-[a-zA-Z0-9\-_]{20,}', "Anthropic API key"),
    (r'sk-proj-[a-zA-Z0-9\-_]{20,}', "OpenAI API key"),
    (r'gho_[a-zA-Z0-9]{36,}', "GitHub OAuth token"),
    (r'ghp_[a-zA-Z0-9]{36,}', "GitHub personal access token"),
    (r'pypi-[a-zA-Z0-9\-_]{50,}', "PyPI API token"),
    (r'xoxb-[a-zA-Z0-9\-]+', "Slack bot token"),
    (r'-----BEGIN (RSA |EC )?PRIVATE KEY-----', "Private key"),
    (r'AIza[0-9A-Za-z\-_]{35}', "Google API key"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key"),
]

# Files that are OK to have key-like patterns (test fixtures, examples)
SAFE_FILES = {
    "tests/", "test_", ".git/", "node_modules/", "__pycache__/",
    "pre_release_check.py",  # this file
    "botocore/data/",  # AWS SDK example files contain sample AKIA keys
    "examples-1.json",  # AWS SDK examples
}

# Directories that should NEVER be in a release artifact
BANNED_DIRS_IN_SPEC = [".opensculpt", ".env", "secrets", ".aws", ".ssh"]

# Files that should NEVER be bundled
BANNED_FILES = ["setup.json", ".env", "credentials.json", "keyring.json"]


def red(s): return f"\033[91m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def green(s): return f"\033[92m{s}\033[0m"
def bold(s): return f"\033[1m{s}\033[0m"


def fail(msg):
    global FAIL
    FAIL = True
    print(f"  {red('FAIL')} {msg}")


def warn(msg):
    print(f"  {yellow('WARN')} {msg}")


def ok(msg):
    print(f"  {green('OK')}   {msg}")


# ═══════════════════════════════════════════
# CHECK 1: Scan tracked files for secrets
# ═══════════════════════════════════════════
def check_secrets_in_tracked_files():
    print(bold("\n[1/7] Scanning tracked files for secrets..."))
    try:
        result = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, cwd=ROOT
        )
        tracked = result.stdout.strip().split("\n")
    except Exception:
        tracked = []

    found = 0
    for fpath in tracked:
        if any(s in fpath for s in SAFE_FILES):
            continue
        full = ROOT / fpath
        if not full.exists() or full.stat().st_size > 500_000:  # skip large files
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern, name in SECRET_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                fail(f"{name} found in {fpath}")
                found += 1

    if found == 0:
        ok("No secrets in tracked files")


# ═══════════════════════════════════════════
# CHECK 2: Scan git history for leaked secrets
# ═══════════════════════════════════════════
def check_git_history():
    print(bold("\n[2/7] Scanning git history for secrets..."))
    found = 0
    for pattern, name in SECRET_PATTERNS[:5]:  # check top patterns
        try:
            result = subprocess.run(
                ["git", "log", "--all", "-p", f"-S{pattern[:20]}", "--", "."],
                capture_output=True, text=True, cwd=ROOT, timeout=30
            )
            if re.search(pattern, result.stdout):
                fail(f"{name} found in git history — needs BFG/filter-branch cleanup")
                found += 1
        except Exception:
            pass

    if found == 0:
        ok("No secrets in git history")


# ═══════════════════════════════════════════
# CHECK 3: PyInstaller spec doesn't bundle secrets
# ═══════════════════════════════════════════
def check_pyinstaller_spec():
    print(bold("\n[3/7] Checking PyInstaller spec for sensitive data..."))
    spec_path = ROOT / "opensculpt.spec"
    if not spec_path.exists():
        warn("No opensculpt.spec found — skipping")
        return

    content = spec_path.read_text(encoding="utf-8")
    # Only check the datas=[] section, not excludes or comments
    datas_match = re.search(r'datas\s*=\s*\[.*?\]', content, re.DOTALL)
    datas_section = datas_match.group(0) if datas_match else ""
    for banned in BANNED_DIRS_IN_SPEC:
        if banned in datas_section:
            fail(f"PyInstaller spec bundles '{banned}' — remove from datas[]")

    for banned_file in BANNED_FILES:
        if banned_file in datas_section:
            fail(f"PyInstaller spec references '{banned_file}' — remove it")

    if not FAIL:
        ok("PyInstaller spec is clean")


# ═══════════════════════════════════════════
# CHECK 4: .opensculpt/setup.json not in dist/
# ═══════════════════════════════════════════
def check_dist_artifacts():
    print(bold("\n[4/7] Checking dist/ for bundled secrets..."))
    found = 0

    # Check PyInstaller output
    for dist_dir in [ROOT / "dist", ROOT / "build"]:
        if not dist_dir.exists():
            continue
        for root, dirs, files in os.walk(dist_dir):
            for f in files:
                if f in BANNED_FILES:
                    fail(f"Sensitive file in dist: {os.path.join(root, f)}")
                    found += 1
                # Also scan content of small files
                fpath = Path(root) / f
                if fpath.suffix in (".json", ".cfg", ".ini", ".env", ".yaml", ".yml"):
                    if fpath.stat().st_size < 100_000:
                        try:
                            content = fpath.read_text(encoding="utf-8", errors="ignore")
                            for pattern, name in SECRET_PATTERNS:
                                if re.search(pattern, content):
                                    fail(f"{name} in dist artifact: {fpath.relative_to(ROOT)}")
                                    found += 1
                        except Exception:
                            pass

    # Check wheel/sdist
    for whl in (ROOT / "dist").glob("*.whl"):
        import zipfile
        with zipfile.ZipFile(whl) as z:
            for name in z.namelist():
                if any(b in name for b in BANNED_FILES):
                    fail(f"Sensitive file in wheel: {name}")
                    found += 1

    for tar in (ROOT / "dist").glob("*.tar.gz"):
        import tarfile
        with tarfile.open(tar) as t:
            for name in t.getnames():
                if any(b in name for b in BANNED_FILES):
                    fail(f"Sensitive file in sdist: {name}")
                    found += 1

    if found == 0:
        ok("No secrets in dist artifacts")


# ═══════════════════════════════════════════
# CHECK 5: .gitignore covers sensitive paths
# ═══════════════════════════════════════════
def check_gitignore():
    print(bold("\n[5/7] Checking .gitignore coverage..."))
    required = [".opensculpt/", ".env", "*.pem", "*.key"]
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        fail(".gitignore missing!")
        return

    content = gitignore.read_text(encoding="utf-8")
    for req in required:
        # Check if the pattern exists (with or without leading slash)
        if req not in content and req.lstrip("/") not in content:
            fail(f".gitignore missing: {req}")
        else:
            ok(f".gitignore covers {req}")


# ═══════════════════════════════════════════
# CHECK 6: Dashboard default auth
# ═══════════════════════════════════════════
def check_dashboard_auth():
    print(bold("\n[6/7] Checking dashboard security..."))
    app_path = ROOT / "agos" / "dashboard" / "app.py"
    if not app_path.exists():
        warn("Dashboard not found")
        return

    content = app_path.read_text(encoding="utf-8")
    if "ApiKeyAuthMiddleware" in content:
        ok("Dashboard has API key auth middleware")
    else:
        warn("Dashboard has no auth middleware")

    if "CORSMiddleware" in content:
        ok("Dashboard has CORS middleware")
    else:
        warn("No CORS middleware — OK for localhost, risky if public")


# ═══════════════════════════════════════════
# CHECK 7: No secrets in README/docs
# ═══════════════════════════════════════════
def check_docs():
    print(bold("\n[7/7] Checking docs for secrets..."))
    found = 0
    for doc in ["README.md", "CLAUDE.md", "ARCHITECTURE.md"]:
        fpath = ROOT / doc
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8", errors="ignore")
        for pattern, name in SECRET_PATTERNS:
            if re.search(pattern, content):
                fail(f"{name} in {doc}")
                found += 1
    if found == 0:
        ok("Documentation is clean")


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
def main():
    print(bold("=" * 60))
    print(bold("  OpenSculpt Pre-Release Security Check"))
    print(bold("=" * 60))

    check_secrets_in_tracked_files()
    check_git_history()
    check_pyinstaller_spec()
    check_dist_artifacts()
    check_gitignore()
    check_dashboard_auth()
    check_docs()

    print(bold("\n" + "=" * 60))
    if FAIL:
        print(red(bold("  BLOCKED — Fix the issues above before releasing.")))
        print(bold("=" * 60))
        sys.exit(1)
    else:
        print(green(bold("  ALL CLEAR — Safe to release.")))
        print(bold("=" * 60))
        sys.exit(0)


if __name__ == "__main__":
    main()
