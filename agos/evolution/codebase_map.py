"""Codebase map — lets the evolution engine understand what modules exist.

Scans agos/ and extracts module docstrings + class names so the LLM
can reason about which component to modify when fixing a demand.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)
_cached_map: str | None = None


def read_codebase_map(root: str = "agos") -> str:
    """Scan agos/ and return module descriptions for LLM context.

    Cached after first call since codebase doesn't change at runtime.
    """
    global _cached_map
    if _cached_map is not None:
        return _cached_map

    modules = []
    root_path = Path(root)
    if not root_path.exists():
        root_path = Path("/app") / root
    if not root_path.exists():
        return "(codebase map unavailable)"

    for py_file in sorted(root_path.rglob("*.py")):
        if "__pycache__" in str(py_file) or py_file.name.startswith("_"):
            continue
        try:
            content = py_file.read_text(errors="ignore")
            tree = ast.parse(content)

            # Extract module docstring
            docstring = ""
            if (tree.body and isinstance(tree.body[0], ast.Expr)
                    and isinstance(tree.body[0].value, (ast.Str, ast.Constant))):
                val = tree.body[0].value
                docstring = (val.s if isinstance(val, ast.Str) else val.value) or ""
                docstring = docstring.split("\n")[0].strip()[:80]

            # Extract class names
            classes = [
                node.name for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
            ]

            rel = str(py_file).replace("\\", "/")
            line = f"  {rel}: {docstring}"
            if classes:
                line += f" [{', '.join(classes[:3])}]"
            modules.append(line)
        except Exception:
            pass

    _cached_map = "\n".join(modules) if modules else "(no modules found)"
    return _cached_map


def reset_cache():
    """Clear cache after code modifications."""
    global _cached_map
    _cached_map = None
