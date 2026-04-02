# OpenSculpt Evolution Context

You are working on OpenSculpt, a self-evolving agentic OS.
The OS generates demand signals when it encounters problems it can't solve.

Before making changes, read:
1. `.opensculpt/DEMANDS.md` — What the OS needs fixed (auto-updated)
2. `ARCHITECTURE.md` — System architecture and wiring rules
3. `.opensculpt/constraints.md` — Environment constraints learned
4. `.opensculpt/resolutions.md` — Past resolution patterns

After making changes:
1. Run: `python -m pytest tests/ -q`
2. Run: `sculpt verify` — checks if demands were resolved
3. Run: `sculpt contribute` — shares your fix with the fleet
