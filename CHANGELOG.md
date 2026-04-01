# Changelog

## [0.1.0] -- 2025-02-15

### Initial Release

- **Intent Engine**: Natural language to execution plans via Claude
- **Agent Kernel**: Async agent lifecycle with state machine (CREATED > READY > RUNNING > PAUSED > TERMINATED)
- **Knowledge System**: Three-weave architecture (episodic + semantic + graph) with unified Loom manager
- **Tool Bus**: Built-in tools (file_read, file_write, shell_exec, http_request, web_search, python_exec)
- **Triggers**: File watch, cron schedule, webhooks with TriggerManager
- **Multi-Agent Coordination**: Channels, shared workspaces, team strategies (solo, pipeline, parallel, debate)
- **Policy Engine**: Tool ACLs, rate limits, token budgets per agent
- **Audit Trail**: Immutable log of every agent action
- **Event Bus**: Pub/sub with wildcard matching and distributed tracing
- **Dashboard**: Real-time web monitoring on localhost:8420
- **Evolution Engine**: Arxiv scanning, paper analysis, code extraction, sandbox testing, proposal integration
- **Ambient Intelligence**: Git watcher, file activity watcher, daily briefing watcher
- **Proactive Engine**: Pattern detection (repetitive edits, failure patterns, frequent tools, idle projects)
- 400 tests passing
