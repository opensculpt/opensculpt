# Contributing to agos

Thanks for your interest in contributing to agos.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/opensculpt/opensculpt.git
cd opensculpt

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check agos/ tests/
```

## Code Style

- Python 3.11+ required
- Line length: 100 characters (ruff enforced)
- Async-first: use `async/await` for I/O operations
- Type hints on all public functions
- Pydantic models for data structures

## Testing

All changes must pass the existing test suite:

```bash
pytest tests/ -v --tb=short
```

New features should include tests. Follow the patterns in `tests/conftest.py` for mocking the LLM provider.

## Pull Request Workflow

1. Branch from `main`
2. Make your changes
3. Run `pytest tests/ -v` and `ruff check agos/ tests/`
4. Submit a PR with a clear description of what changed and why

## Adding Integration Strategies

To add a new evolution integration strategy, follow the pattern in `agos/evolution/strategies/`:

1. Create a new file `agos/evolution/strategies/your_strategy.py`
2. Subclass `IntegrationStrategy` from `agos/evolution/integrator.py`
3. Implement: `validate()`, `snapshot()`, `apply()`, `rollback()`, `health_check()`
4. Register it in `agos/cli/context.py`
5. Add tests in `tests/evolution/`

## Project Structure

```
agos/
  cli/          # Typer CLI (natural language routing)
  kernel/       # Agent runtime (state machine, lifecycle)
  llm/          # LLM providers (Anthropic Claude)
  intent/       # Intent engine + proactive intelligence
  tools/        # Tool registry + builtins
  knowledge/    # Three-weave knowledge system (The Loom)
  coordination/ # Multi-agent teamwork
  triggers/     # File watch, schedule, webhook triggers
  ambient/      # Always-on background watchers
  policy/       # Safety policies + audit trail
  events/       # Event bus + distributed tracing
  evolution/    # Self-evolving R&D engine
  dashboard/    # Web monitoring UI
```

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
