"""Database migration system for agos.

Tracks schema versions and applies migrations sequentially.
Each migration is a Python module with an `upgrade()` async function.
"""
