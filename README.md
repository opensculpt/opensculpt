# OpenSculpt — The Self-Evolving Agentic OS

[![PyPI version](https://img.shields.io/pypi/v/opensculpt)](https://pypi.org/project/opensculpt/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/github/actions/workflow/status/opensculpt/opensculpt/ci.yml?label=tests)](https://github.com/opensculpt/opensculpt/actions)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

**Every failure is a chisel strike. Every fix reveals a better shape.** 🐧🪨

OpenSculpt is not another agent framework. It's an operating system for intelligence. You speak naturally, it reasons, plans, spawns agents, executes, remembers, and evolves — driven by what you actually need, not random research papers. Claude Code, Cursor, Windsurf are the chisels. The OS is the stone. Like Tux represents Linux, Chip (the self-sculpting penguin) represents an OS that shapes itself.

```bash
sculpt "why is my API slow?"
# → Spawns analyst agent, profiles endpoints, checks logs, reports findings

sculpt "write a REST API for user management with tests and docs"
# → Assembles a team: architect → coder → [tester + documenter in parallel]
```

## Install

### pip (recommended)

```bash
pip install opensculpt
```

### Windows Executable

Download `OpenSculpt.exe` from the [latest release](https://github.com/opensculpt/opensculpt/releases/latest).

### From Source

```bash
git clone https://github.com/opensculpt/opensculpt.git
cd agenticOS
pip install -e ".[dev]"
```

## Quick Start

```bash
# Set your API key
export SCULPT_ANTHROPIC_API_KEY=your-key-here

# Initialize workspace
sculpt init

# Talk to it
sculpt "analyze my codebase and summarize what each module does"
sculpt "find all TODO comments and prioritize them"
sculpt "review this function for bugs" < src/main.py
```

## Architecture

```
+------------------------------------------------------------------+
|                         OpenSculpt                                  |
|                                                                   |
|   INTERFACE    Natural Language CLI  |  Dashboard  |  SDK         |
|                         |                                         |
|   SOUL         Intent Engine (understand → plan → execute)        |
|                         |                                         |
|   BRAIN        Agent Kernel (lifecycle, state, budget)            |
|                         |                                         |
|   MEMORY       Knowledge System (episodic + semantic + graph)     |
|                         |                                         |
|   BODY         Tool Bus (file, shell, HTTP, Python, web search)   |
|                         |                                         |
|   SENSES       Triggers (file watch, cron, webhooks)              |
|                         |                                         |
|   SOCIAL       Coordination (channels, teams, debate protocol)    |
|                         |                                         |
|   IMMUNE       Policy Engine + Audit Trail                        |
|                         |                                         |
|   EVOLUTION    Demand-Driven Self-Evolution (failures → research  |
|                → code generation → sandbox → deploy)              |
+------------------------------------------------------------------+
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `sculpt "<intent>"` | Natural language — the OS figures out what to do |
| `sculpt ps` | List running agents |
| `sculpt recall "<topic>"` | Search knowledge system |
| `sculpt timeline` | View event history |
| `sculpt watch <path> "<intent>"` | Watch files, trigger agent on changes |
| `sculpt schedule <interval> "<intent>"` | Run agent on a schedule |
| `sculpt team "<task>"` | Multi-agent team execution |
| `sculpt evolve` | Run R&D cycle (scan arxiv, analyze, propose) |
| `sculpt evolve --proposals` | View pending evolution proposals |
| `sculpt ambient --start` | Start background watchers |
| `sculpt proactive --scan` | Run pattern detection |
| `sculpt audit` | View audit trail |
| `sculpt policy` | Configure safety policies |
| `sculpt dashboard` | Launch web monitoring UI |
| `sculpt update` | Check for updates and self-update |
| `sculpt version` | Show version |

## Configuration

All settings via environment variables with `SCULPT_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `SCULPT_ANTHROPIC_API_KEY` | (required) | Your Anthropic API key |
| `SCULPT_DEFAULT_MODEL` | `claude-sonnet-4-20250514` | Claude model to use |
| `SCULPT_WORKSPACE_DIR` | `.opensculpt` | Local workspace directory |
| `SCULPT_MAX_CONCURRENT_AGENTS` | `50` | Max agents running at once |
| `SCULPT_DASHBOARD_PORT` | `8420` | Dashboard web UI port |
| `SCULPT_LOG_LEVEL` | `INFO` | Logging level |

## Self-Evolution

OpenSculpt evolves itself based on what you actually need:

1. **Detect** — Collects demand signals from user failures, tool errors, missing capabilities, agent crashes
2. **Scout** — Searches arxiv for papers that address actual problems (not random topics)
3. **Analyze** — Extracts actionable techniques from each paper
4. **Generate** — LLM writes code implementing the technique, with demand context injected
5. **Test** — Runs code patterns in a sandboxed environment
6. **Deploy** — Applies sandbox-passing code as live evolved strategies
7. **Learn** — Records what worked and what failed for cross-cycle learning

```bash
sculpt evolve                    # Run a full R&D cycle
sculpt evolve --proposals        # Review what it found
sculpt evolve --accept <id>      # Accept a proposal
sculpt evolve --apply <id>       # Apply it (with auto-rollback on failure)
```

**Every failure is a chisel strike. Every fix reveals a better shape.** The OS sculpts itself — shaped by AI coding tools on every user's machine.

## Security

OpenSculpt is an **operating system** — it executes shell commands, writes files, makes HTTP requests, and manages Docker containers by design. This is equivalent to giving it `sudo` access.

**Before exposing to a network:**
- Set `SCULPT_DASHBOARD_API_KEY` to a strong random value
- Set `SCULPT_APPROVAL_MODE` to `confirm-dangerous` (default is `auto`)
- Do **not** expose port 8420 to the public internet without auth

**By design, the OS agent can:**
- Execute arbitrary shell commands and Python code
- Read/write any file on the filesystem
- Make HTTP requests to any URL
- Manage Docker containers via the Docker socket

This is intentional — it's an OS, not a sandboxed app. Treat it like SSH access to a server.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v          # 779+ tests
ruff check agos/ tests/   # lint
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
