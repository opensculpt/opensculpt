# Contributing to OpenSculpt

Thanks for your interest in contributing to OpenSculpt!

## Quick Setup

```bash
git clone https://github.com/opensculpt/opensculpt.git
cd opensculpt
pip install -e ".[dev]"
```

Or use [GitHub Codespaces](https://codespaces.new/OpenSculpt/opensculpt?quickstart=1) for a zero-install environment.

## Development Workflow

1. **Fork and branch** — create a feature branch from `main`
2. **Make changes** — edit code in `agos/`
3. **Lint** — `ruff check agos/ tests/` (must pass, CI enforces this)
4. **Test** — `pytest tests/ --ignore=tests/test_frontend_playwright.py --ignore=tests/test_user_stories.py -q`
5. **Submit PR** — fill out the PR template, link any related issues

## Code Style

- Python 3.11+ required
- Line length: 100 characters (ruff enforced)
- Async-first: use `async/await` for I/O operations
- Type hints on public functions
- Pydantic models for data structures

## Project Structure

```
agos/                  # Main package
├── kernel/            # Agent runtime, state machine
├── os_agent.py        # Claude-powered brain
├── dashboard/         # FastAPI web UI
├── evolution/         # Self-evolution engine
├── knowledge/         # Memory system (TheLoom)
├── daemons/           # Background workers (goal runner, GC, domain)
├── cli/               # CLI commands
├── tools/             # Built-in tools
├── hands/             # Autonomous background tasks
├── mcp/               # Model Context Protocol integration
└── a2a/               # Agent-to-Agent protocol
tests/                 # Test suite
.opensculpt/           # Runtime workspace (gitignored)
```

## Testing

```bash
# Unit tests (fast, no server needed)
pytest tests/ --ignore=tests/test_frontend_playwright.py --ignore=tests/test_user_stories.py -q

# Playwright E2E tests (needs server running)
python -m agos.serve &
pytest tests/test_frontend_playwright.py -v

# Lint
ruff check agos/ tests/
```

## What to Work On

Check [open issues](https://github.com/opensculpt/opensculpt/issues) or the roadmap in `CLAUDE.md` for known bugs and planned features.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
