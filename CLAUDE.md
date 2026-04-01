# OpenSculpt — The Self-Evolving Agentic OS

## Identity

OpenSculpt is an **operating system**, not an application. Think of it like Linux, not like a todo app.

- It manages agents like an OS manages processes
- It has a kernel (AgentRuntime), a shell (OSAgent), a file system (TheLoom), an event bus, audit trail, and policy engine
- Agents are first-class citizens — they get spawned, scheduled, supervised, killed, and resource-limited
- The OS agent is the brain — it uses Claude to reason about ANY command and executes it with real tools (shell, files, HTTP, python, sub-agents, hands)
- Everything else (evolution engine, security scanner, code analyst, etc.) are sub-agents or system services
- **"Every failure is a chisel strike."** — when something fails, the OS detects the gap and sculpts a fix. Claude Code, Cursor, Windsurf are the chisels. The OS is the stone. Like Tux represents Linux, Chip (the self-sculpting penguin) represents an OS that shapes itself.

## Architecture (see ARCHITECTURE.md for full Mermaid diagrams)

**CRITICAL: Read ARCHITECTURE.md before making any changes.** It contains:
- Complete component wiring diagram (Mermaid)
- Data flow sequence diagram for "run sales for my startup"
- Component status: working / partial / ghost
- **Wiring Rules table** — when X happens, call Y. Every row is a contract.

**After every code change, update ARCHITECTURE.md:**
1. Move components between Working / Partial / Ghost as status changes
2. Add new wiring rules to the table
3. Verify no existing wiring rule was broken

**Also read SCENARIO_WIRING.md** — it defines exactly which components must fire for each scenario. The component usage matrix shows which components are critical vs optional per scenario. Wire components in priority order defined at the bottom of that file.

- **Boot**: `agos/boot.py` — OS boot sequence, state restore, launches agents + evolution
- **Serve**: `agos/serve.py` — Docker entry point, wires dashboard + boot together
- **Kernel**: `agos/kernel/` — AgentRuntime, Agent, state machine
- **OS Agent**: `agos/os_agent.py` — Claude-powered brain, handles all user commands
- **Knowledge**: `agos/knowledge/` — TheLoom (episodic, semantic, graph memory)
- **Processes**: `agos/processes/` — ProcessManager, WorkloadDiscovery, AgentRegistry
- **Agents**: `agos/agents/` — System agent tasks (security, profiling, cleanup) + lifecycle
- **Hands**: `agos/hands/` — Autonomous background tasks (researcher, monitor, digest, scheduler)
- **Guard**: `agos/guard.py` — Loop detection + capability gates (from OpenFang)
- **Session**: `agos/session.py` — Conversation compaction (from OpenClaw)
- **Demand**: `agos/evolution/demand.py` — Demand-driven evolution signals
- **Evolution**:
  - `agos/evolution/cycle.py` — Evolution cycle orchestrator + continuous loop
  - `agos/evolution/tool_evolver.py` — Generates new tools from demand signals
  - `agos/evolution/demand.py` — Collects failure signals from user activity
  - `agos/evolution/codegen.py` — Code generation, evolved strategy loading
  - `agos/evolution/sandbox.py` — Sandbox validation (static analysis + subprocess isolation)
  - `agos/evolution/meta.py` — MetaEvolver, ALMA-style parameter mutations
- **Dashboard**: `agos/dashboard/app.py` — FastAPI web UI at port 8420
- **MCP**: `agos/mcp/` — Model Context Protocol integration for external tool servers
- **A2A**: `agos/a2a/` — Agent-to-Agent protocol for cross-OS communication

## Key Principles

1. The OS agent can do ANYTHING — it has shell, file, HTTP, python, sub-agents, and hands
2. Sub-agents run their own Claude loops in parallel
3. The evolution engine is always running — driven by DEMAND SIGNALS from user failures, not random papers
4. All actions are audited. All events flow through the EventBus.
5. Docker is the deployment target — `docker compose up` boots the entire OS
6. Be frugal — minimize system-level token and compute consumption
7. **Everything must be real. No fake demos, no placeholder tools, no cosmetic evolution.**
8. **LLM-native, not database-native.** This OS is powered by LLMs. Store knowledge as `.md` files — the LLM reads text and understands it. That IS the search engine. No SQLite indexes, no TF-IDF, no cosine similarity for knowledge retrieval. Use databases only when you need transactional guarantees or millions of rows. Constraints, resolutions, skills, and learnings are all `.md` files in `.opensculpt/` that Claude Code reads directly.
9. **Claude Code is the meta-evolution engine.** The OS evolves through Claude Code (or any vibe coding tool) reading demands, reading research, understanding the codebase, and writing real code. 100 users = 100 Claude Code instances evolving the OS simultaneously. Federation shares the KNOWLEDGE (as .md files), not the code — each instance's Claude Code writes env-appropriate code.

## CRITICAL: Playwright-First Verification

**Every UI or feature change MUST be verified via Playwright — not just pytest.**

1. **Launch the dashboard** via Playwright (`browser_navigate` to `http://localhost:8420`)
2. **Take a screenshot** showing the actual user-facing result
3. **Simulate the user journey**: click buttons, expand panels, send commands, check responses
4. **Show the screenshot as proof** — pytest passing is NOT sufficient for UI work

If you can't Playwright-test (server not running), say "deployed but unverified via Playwright" — never claim it works from code alone.

For CLI changes: run the actual command and show the terminal output.
For API changes: curl the endpoint and show the response.
For dashboard changes: screenshot via Playwright. Period.

## CRITICAL: Realistic Scenario Testing

**Before any release, the OS must be tested against real-world scenarios from SCENARIOS.md.**

The top 5 scenarios to test:
1. **Sales CRM Operator** — install CRM, create leads, manage pipeline, follow up
2. **Customer Support Manager** — install helpdesk, classify tickets, auto-reply
3. **Internal Knowledge System** — ingest documents, semantic search, knowledge graph
4. **DevOps Operator** — set up CI/CD, deploy, monitor, recover
5. **Company-in-a-Box** — connect multiple systems, unified workflows

### How to test a scenario (4 phases):

**Phase 1 — Setup**: Can the OS install and configure real software?
- Deploy in Docker with `docker compose up`
- Ask the OS agent to install the system (e.g., "Install EspoCRM for my sales team")
- Verify: containers running, software accessible, credentials working

**Phase 2 — Ingestion**: Can the OS bring in data?
- Ask the OS to create records, import data, connect sources
- Verify: data exists in the actual system (query via API)

**Phase 3 — Operation**: Can the OS maintain workflows?
- Ask the OS to perform daily tasks (follow up on leads, check tickets)
- Verify: actions taken, results visible in the system

**Phase 4 — Evolution**: Did the OS evolve during this process?
- Check `/api/evolution/demands` — were capability gaps detected?
- Check `/api/evolution/changelog` — were new tools/strategies deployed?
- Check the Agents tab — are spawned agents visible?
- Check audit trail — are all actions logged?

### Known Gaps (Fix These)

These are real gaps exposed by scenario testing. Every developer must know them:

1. **Sub-agents don't appear in Agents tab** — `spawn_agent` creates in-memory tasks, not registered agents. Wire them into the AgentRegistry.
2. **No browser tool** — the OS cannot interact with web UIs. Need a built-in Playwright tool or MCP connection to `@playwright/mcp`.
3. **No native docker tool** — the OS uses `shell("docker ...")` as a workaround. Need a structured docker tool with proper error handling.
4. **OS agent actions not in audit trail** — tool calls go through events but aren't logged to the audit table.
5. **Evolution generates toy code** — FIXED: ToolEvolver now checks for builtins first (docker, browser), tries MCP discovery second, LLM generation last.
6. **Shell success hides failures** — FIXED: `_real_ok` detection + `os.capability_gap` event + demand collector pattern matching for exit!=0.
7. **No persistent multi-step tasks** — FIXED: `agos/task_planner.py` provides checkpointed multi-step execution with persistence to disk.

### Testing Commands

```bash
# Run all unit tests (800+)
python -m pytest tests/ --ignore=tests/test_frontend_playwright.py --ignore=tests/test_user_stories.py -q

# Run Playwright frontend tests (needs server running)
python -m agos.serve &
python -m pytest tests/test_frontend_playwright.py tests/test_user_stories.py -v

# Run evolution scenario tests
python -m pytest tests/test_evolution_scenarios.py -v

# Run full scenario test (Docker)
docker compose up -d
curl -X POST http://localhost:8420/api/os/command -H "Content-Type: application/json" \
  -d '{"command": "Install EspoCRM using Docker on port 8081"}'
# Then verify: curl http://localhost:8081 returns 200
```

## MANDATORY: End-to-End Scenario Verification

**Every change to the OS agent, evolution, tools, or dashboard MUST be verified by running actual scenarios from SCENARIOS.md end-to-end via Playwright.** This is non-negotiable.

### What "End-to-End" Means (the FULL user journey)

1. **Send command via UI** (Playwright) — not curl, not API. Use the actual command bar.
2. **Wait for OS response** — did it ask business questions? Did it create a goal?
3. **Watch goal execute** — phases progress, status line updates, activity feed shows events
4. **Verify the result WORKS** — curl the API, check the database, hit the endpoint
5. **Test UI interactions** — click completion chips, expand cards, dock daemons, check health
6. **Restart container** — does the service survive? Does goal_runner auto-restart it? Does UI show truth?
7. **Check service health** — topbar shows "● N services", completion card shows "Services running" or "Service Down"

### What to Check at Each Phase

| Phase | What to verify | How |
|-------|---------------|-----|
| **Setup** | Service deployed + accessible | `curl http://localhost:PORT/health` from inside container |
| **Ingestion** | Data imported/created | Query the API, check record count |
| **Operation** | User can use the system | Create a ticket, add a lead, run a query |
| **Evolution** | OS learned from the process | Check `/api/evolution/changelog` for principles |
| **Service Health** | Services survive restart | `docker restart`, wait, verify port responds |

### Never Claim PASS Without

- Actually completing the full scenario (not just sending the command)
- Verifying the deployed service responds with real data
- Testing that UI buttons/chips actually work (not just render)
- Confirming services survive container restart

### Quick Smoke Test

```bash
# 1. Boot and verify
docker compose up -d && sleep 10
curl -s http://localhost:8420/api/status | python -m json.tool

# 2. Send a CRM install command
curl -s -X POST http://localhost:8420/api/os/command \
  -H "Content-Type: application/json" \
  -d '{"command": "Install EspoCRM for sales team using Docker"}' \
  --max-time 120

# 3. Check audit trail logged the actions
curl -s http://localhost:8420/api/audit | python -m json.tool | head -20

# 4. Check demand signals
curl -s http://localhost:8420/api/evolution/demands | python -m json.tool

# 5. Check agents tab shows spawned agents
curl -s http://localhost:8420/api/agents | python -m json.tool

# 6. Verify CRM is actually running
docker exec opensculpt curl -s http://localhost:8081/api/v1/Settings | head -1
```

### What "Working" Means

- **OS agent uses native tools**: If docker_run/browser/http tools exist, the agent MUST prefer them over shell("docker ..."). Check the audit trail — tool_name should be "docker_run" not "shell".
- **Demand signals fire**: If the agent falls back to shell for docker commands, `/api/evolution/demands` MUST show a `capability_gap` signal.
- **Sub-agents are visible**: If spawn_agent is called, the Agents tab MUST show the sub-agent with its task description.
- **Evolution is real**: If demand signals exist, the next evolution cycle MUST attempt to resolve them (check evolution logs for "Demand → tool request" or "evolution.builtin_activated").

## Evolution: MUST BE REAL, NEVER COSMETIC

- **Evolved code MUST actually execute.** No placeholder `apply()` methods.
- **Demand signals drive evolution.** When the OS agent uses `shell("docker ...")` as a workaround, that's a `os.capability_gap` event that should eventually produce a native docker tool.
- **Evolution from MCP** — when the OS needs a capability (browser, database), it should discover and connect to MCP servers that provide it, not generate toy Python functions.
- **Every evolved strategy must change real behavior** — call the function, check the output.
- **Delete what doesn't work** — if a pattern fails sandbox, remove it.
- **No fake versioning** — only write new version if code actually changed.
- **Verify with real assertions** — prove behavior changed after evolution.

## Evolution Quality: No Junk Papers

- **Reject non-CS papers** — arxiv categories must include `cs.*`
- **Require 2+ keyword matches** from TECHNIQUE_PATTERNS
- **Negative keyword filtering** — reject physics/bio/math papers
- **Methodology must be implementable** — concrete techniques, not theoretical analysis
- **Don't blindly pair seed code with random papers** — the connection must be real

## ROADMAP: Known Bugs & Pending Work (Updated 2026-03-26)

Everything below was found by running Scenario 1 ("run sales for my company") live in Docker via Playwright. These are real bugs, not hypothetical.

### P0 — OS is Lying (Backend Integrity)

| # | Bug | File | Status |
|---|-----|------|--------|
| B1 | **Green ticks on failures** — Three-tier verification: auto (shell check), ask_user (confirmation), none (honest yellow). LLM plans verify at goal creation time. Smart diagnosis rewrites bad verify commands. | `agos/daemons/goal_runner.py` | DONE |
| B2 | **Sub-agents spawn recursive goals** — Blocked set_goal, spawn_agent, check_goals from sub-agent tool list. | `agos/os_agent.py` `_run_sub_agent()` | DONE |
| B3 | **Evolution infinite loop** — Clear capability_gap, missing_tool, tool_fail keys after builtin activation. | `agos/evolution/cycle.py` | DONE |
| B4 | **"14 improvements" = 0 improvements** — Evolution counts "reviewed, no changes needed" as an improvement. | `agos/evolution/state.py` + `agos/dashboard/app.py` | TODO |
| B5 | **DomainDaemons burn tokens forever** — Backoff after 10 empty fast_checks → pause daemon. | `agos/daemons/domain.py` | DONE |
| B6 | **Phase results are raw LLM rambling** — Strip "Let me", "Now I'll" etc. prefixes, keep outcome lines. | `agos/daemons/goal_runner.py` | DONE |

### P1 — Dashboard UX (User Runs Away)

| # | Issue | File | Status |
|---|-------|------|--------|
| U1 | **OS Shell is a 1990s terminal** — single-line input, monospace wall of text, no conversation history, no streaming, no markdown rendering. Needs modern chat UI (bubbles, streaming, history). | `agos/dashboard/app.py` | TODO |
| U2 | **No cross-linking** — resources show `goal_1774554127_2682`, agents don't link to phases, daemons don't link to goals. Everything is disconnected tables. | `agos/dashboard/app.py` | TODO |
| U3 | **Overview should be goal-centric** — instead of 5 flat tables (Agents, Goals, Resources, Daemons, Vitals), show one tree: Goal → Phases → Agents/Resources/Daemons per phase. | `agos/dashboard/app.py` | TODO |
| U4 | **25+ resources = noise** — every config file is a "resource." Should group by service: "EspoCRM Deployment (6 files)" not 6 individual rows. | `agos/dashboard/app.py` + `agos/os_agent.py` | TODO |
| U5 | **Toast spam** — Cap at 3 visible, auto-dismiss 3s. | `agos/dashboard/app.py` | DONE |
| U6 | **refreshOverview JS error** — Fixed: calls refreshFast() now. | `agos/dashboard/app.py` | DONE |
| U7 | **Tools tab broken** — Fall back to OS agent tool registry. | `agos/dashboard/app.py` | DONE |
| U8 | **Setup tab overwhelm** — 27 providers, 42 channels dumped raw. | `agos/dashboard/app.py` | FIXED (collapse) |
| U9 | **System Vitals 0%** — CPU/RAM/Disk not collecting in Docker. | `agos/dashboard/app.py` | FIXED (shows "collecting...") |

### P2 — Evolution Quality

| # | Issue | File | Status |
|---|-------|------|--------|
| E1 | **Arxiv papers irrelevant to user's domain** — user doing sales, evolution reads "Anti-I2V video safeguarding." Should filter by active goal category. | `agos/evolution/cycle.py` + `agos/evolution/scout.py` | TODO |
| E2 | **"0 Problems Detected" when 5 exist** — Now updates evo-demands-count from active_demands. | `agos/dashboard/app.py` | DONE |
| E3 | **Demands not domain-aware** — should escalate to "infrastructure missing" after repeated failures, tell user what to fix. | `agos/evolution/demand.py` | TODO |

### Already Done (This Session)

| # | What | Status |
|---|------|--------|
| D1 | DomainDaemon class — LLM-powered background workers with fast_check gating | DONE |
| D2 | GoalRunner spawns DomainDaemons instead of dumb scheduler | DONE |
| D3 | Fix `_run_sub_agent` LLMMessage import shadowing | DONE |
| D4 | GoalRunner starts first phase immediately (no 5-min wait) | DONE |
| D5 | Fix DaemonResult field name (hand_name → daemon_name) | DONE |
| D6 | Expandable goal rows with phase details in Overview | DONE |
| D7 | Toast notifications for goal phase events | DONE |
| D8 | Overview daemons section populated from /api/daemons | DONE |
| D9 | Stat card shows actual goal count not evolution cycles | DONE |
| D10 | Stop tracking /tmp files as resources | DONE |
| D11 | Collapse providers/channels in Setup tab | DONE |
| D12 | .opensculpt/ added to .dockerignore for clean builds | DONE |
| D13 | test_scenario_live.py — Playwright + API scenario tests | DONE |
| D14 | ARCHITECTURE.md + SCENARIO_WIRING.md updated | DONE |
| D15 | Environment probe module (`agos/environment.py`) — detects OS, container, Docker, pkg managers, runtimes, services, permissions, limits, running services, recommends deployment strategy | DONE |
| D16 | Environment injected into GoalRunner planning + sub-agent prompt | DONE |
| D17 | Smart GoalRunner diagnosis — diagnoses verify failures, rewrites bad verify commands, distinguishes "verify wrong" vs "work failed" | DONE |
| D18 | GoalRunner tick 300s → 30s (user was waiting 5 min per retry) | DONE |
| D19 | Scenarios 6-20 fleshed out with individual user scenarios (file organizer, finance tracker, research, life operator) | DONE |
| D20 | Verification tiers: auto/ask_user/none with environment-aware planning | DONE |
| D21 | EVO1: DemandSolver takes real actions (patch_code, create_tool, distill_principle, tell_user) — persistent across cycles with attempt tracking | DONE |
| D22 | EVO2: ToolEvolver LLM reasoning — env summary + codebase map + demand context injected into tool generation prompt | DONE |
| D23 | EVO3: Dashboard "Needs Your Help" card — `/api/evolution/blockers` endpoint, event bus subscription, dismiss buttons | DONE |
| D24 | Demand lifecycle — DemandSignal has status (active/attempting/escalated/resolved), exponential backoff, auto-escalation after 6 attempts | DONE |
| D25 | DemandSolver wired to ToolEvolver in evolution_loop — persistent solver instance, passes tool_evolver for direct tool creation | DONE |
| D26 | 5-scenario docker-compose (`docker-compose.5scenarios.yml`) + launch/harvest scripts for overnight testing | DONE |

### THE REAL PROBLEM — Evolution engine is stupid (PARTIALLY FIXED)

The evolution engine has an LLM but never uses it to THINK. It should look at all existing modules, the demands, the environment, and reason: "What's missing? What module would fix this pattern of failures?" Instead it:
- Scans random arxiv papers (video security while user needs CRM)
- Checks if a builtin tool name matches a demand keyword (string matching, not reasoning)
- Generates toy Python functions that nobody uses
- Never looks at the existing codebase to understand what's already there
- Never asks "why do sub-agents keep failing?" — just keeps activating the same tool

**What it SHOULD do:** Use the LLM to analyze demands + environment + existing modules → reason about what strategy/module/fix would solve the pattern → generate it → test it → deploy it. Like a senior engineer debugging, not a script matching keywords.

Example: Demands show "Docker not available" 6 times. Evolution should:
1. Read the environment probe → "container, no Docker daemon, apt-get available"
2. Read the existing modules → "sub-agent prompt doesn't mention environment"
3. Reason: "Sub-agents fail because they don't know the environment. I need to inject env info into their prompts."
4. Generate the fix (or build a module like environment.py)
5. Test it
6. Deploy it

Instead it scans arxiv for papers about "container orchestration" and activates a dormant Docker tool that also can't connect to the daemon.

| # | Issue | File | Status |
|---|-------|------|--------|
| EVO1 | **Evolution doesn't use LLM to reason about demands.** DemandSolver now takes REAL actions: `patch_code` (delegates to SourcePatcher), `create_tool` (delegates to ToolEvolver with LLM reasoning), `distill_principle`, `tell_user`. Persistent across cycles with attempt tracking + escalation after 5 failures. | `agos/evolution/demand_solver.py` + `agos/evolution/cycle.py` | DONE |
| EVO2 | **Evolution doesn't know what modules exist.** ToolEvolver `_generate_with_llm` now receives environment summary + codebase map + demand context so the LLM reasons about what to build for THIS environment. Fuzzy builtin matching. Checks registry before generating duplicates. | `agos/evolution/tool_evolver.py` | DONE |
| EVO3 | **Evolution doesn't communicate with the user.** Dashboard now has "Needs Your Help" card (yellow border, dismissable). `/api/evolution/blockers` endpoint. Event bus subscription on `evolution.user_action_needed`. Auto-escalation after 6 failed attempts. | `agos/evolution/demand.py` + `agos/dashboard/app.py` | DONE |

### GAPS DISCOVERED DURING SCENARIO 1 TESTING (for future evolver design)

These are the specific self-correction gaps the OS needs to evolve:

| # | Gap | What should have happened | Design implication |
|---|-----|--------------------------|-------------------|
| G1 | **Verify command references Docker in non-Docker env** — LLM wrote `curl ... && docker ...` as verify even though env probe says no Docker | GoalRunner should detect verify/env mismatch and rewrite verify command | Evolver needs: "when verify fails, diagnose if the verify itself is wrong vs the work" |
| G2 | **Sub-agent tried Docker despite env probe saying no Docker** — first attempt still used docker commands before falling back to apt | Sub-agent prompt needs stronger env-awareness. The environment probe was available but the LLM partially ignored it | Evolver needs: "learn from failed approaches and stop trying them" |
| G3 | **Phase marked failed but work actually succeeded** — CRM was running (curl 200, nginx+mysql+php processes up) but phase showed failed due to bad verify | GoalRunner needs to cross-check: if verify fails but there's evidence of success (processes running, ports open), investigate before declaring failure | Evolver needs: "evidence-based diagnosis, not binary pass/fail" |
| G4 | **Port not exposed to user** — CRM running on 8080 inside container but only 8420 mapped to host. User can never reach it | OS should detect this and either proxy through 8420 or tell user to remap ports | Evolver needs: "think about the full user journey, not just deployment" |
| G5 | **No user notification of success** — CRM deployed but user sees "retrying" in dashboard, no link to the CRM, no "your CRM is ready at..." | After successful deployment, OS should post a message with the URL and credentials | Evolver needs: "last mile delivery — tell the user their thing is ready" |
| G6 | **Demand loop: builtin activation repeated every cycle** — FIXED: DemandSignal now has lifecycle (active → attempting → escalated → resolved) with exponential backoff (1min, 2min, 4min...) and auto-escalation after 6 attempts. `clear_resolved` marks as resolved instead of deleting. `has_demands()` respects lifecycle. | Evolver needs: "demand lifecycle — detect → attempt → resolve/escalate" | DONE |
| G7 | **Evolution papers irrelevant to active scenario** — User doing sales CRM, evolution reads about video security and ASR bias | Evolution topic selection should be influenced by active goals and demands | Evolver needs: "domain-aware research — what papers help the CURRENT task?" |
| G8 | **Environment probe didn't exist until hardcoded** — The OS should have detected it needed env awareness and built the probe itself | Evolution should create utility modules when repeated failures suggest a pattern | Evolver needs: "meta-capability — evolve the ability to probe before evolving tools" |
| EVO2 | **Evolution doesn't adapt to environment.** FIXED: ToolEvolver `_generate_with_llm` now injects `EnvironmentProbe.summary()` + `codebase_map` so LLM designs tools for the actual environment. | `agos/evolution/tool_evolver.py` | DONE |
| EVO3 | **Evolution doesn't communicate with the user.** FIXED: Dashboard "Needs Your Help" card + `/api/evolution/blockers` endpoint + auto-escalation after 6 attempts. | `agos/evolution/demand.py` + `agos/dashboard/app.py` | DONE |

### Priority Order for Remaining Work

**DONE (evolution works):**
1. ~~EVO1 — Make evolution solve real demands, not scan arxiv~~ DONE
2. ~~EVO2 — Environment-aware problem solving~~ DONE
3. ~~EVO3 — Tell user when OS needs help~~ DONE
4. ~~G6 — Demand lifecycle with backoff + escalation~~ DONE

**Do FIRST (remaining integrity):**
1. B4 — Honest improvement stats (only count actual code changes)
2. E1 — Arxiv papers irrelevant to user's domain (filter by active goal category)
3. E3 — Demands domain-aware escalation

**Do second (UX overhaul):**
4. U1 — Chat shell redesign (modern chat UI)
5. U3 — Goal-centric overview (one tree, not 5 tables)
6. U2 — Cross-linking entities
7. U4 — Resource grouping

**Do last (polish):**
8. B5 — DomainDaemon backoff (already partially done)
9. U5 — Toast rate limiting
10. U7 — Fix Tools tab

### NEXT EVOLUTION: Unix-Style Resource Management + Federated Fleet Learning

#### P0 — Resource Scoping (Production OOM bug, 2026-03-28)

10 containers ran overnight, each spawned orphaned Docker containers that were never cleaned up. Memory exhausted, Docker OOM-killed everything. Windows warned the user — OpenSculpt didn't even notice.

**Already fixed (committed):**
- GC memory pressure monitor (os.memory_critical/warning events)
- `_gc_docker_containers()` implemented (was called but never defined)
- Phase-scoped cleanup before retry (Unix kill -PGID)
- Shell command resource auto-detection (docker run → auto-register)
- GC auto-started on boot (dry_run=False)
- Memory limits on spawned containers (--memory 512m) and opensculpt containers (1G)

**Next: Deep resource architecture (inspired by K8s + DB transactions):**

| Pattern | Unix/K8s Equivalent | OpenSculpt Implementation |
|---------|---------------------|--------------------------|
| Hierarchical ownership | K8s ownerReferences | `Resource.owner_refs` → Goal → Phase → Resource chain |
| Savepoints | DB SAVEPOINT/ROLLBACK | Snapshot registry before each phase, rollback on failure |
| Compensating transactions | Saga pattern | Phase-level compensation log, reverse-order cleanup |
| Finalizers | K8s finalizers | Pre-delete hooks for resources needing confirmed cleanup |
| Drift detection | Terraform plan | `reconcile()` finds tracked≠actual, auto-corrects |

#### P1 — Federated Fleet Learning (Agentic SRE pattern)

**Architecture for 100+ user fleet:**

| Layer | What | Speed | Cost |
|-------|------|-------|------|
| L1: Local Evolution | Haiku inside each container (prompt rules, skill docs) | 15-30s | $0.10/day |
| L2: Fleet Gossip | Peer-to-peer sharing (sandbox-validated, context-tagged) | 60-120s | Free |
| L3: Claude Code Curator | `/loop` monitors fleet, measures fix efficacy, packages releases | 10min | $2-3/night |
| L4: Trusted Releases | Git-versioned, scenario-gated, auto-updated | Daily | Free |

**Key insight**: Share recipes (inspectable code + principles), not weights. Like K8s OperatorHub, not federated ML.

**Research backing (2026-03-28):**
- GEA paper: group evolution = 71% vs 57% individual (3.5x faster)
- Azure SRE Agent: 1,300+ agents, 35K incidents/month mitigated
- VIGIL paper: out-of-band supervisor (= Claude Code /loop)
- Tesla fleet learning: 27-35% improvement per quarterly cycle

**Fleet sync already exists** (`agos/evolution/sync.py`): gossip protocol, sandbox validation, context tagging by scenario + environment. Missing: curator layer, efficacy measurement, trusted release pipeline, reputation system.

#### P2 — Evolution Must Patch Real Code, Not Generate Dead Scripts

Overnight 6 containers produced 14 evolved `.py` files — none were used. Evolution always picks the easy path (skill docs, prompt rules) because:
- EvolutionAgent prompt says "Start with #1 or #2. Only try #4 if simpler fixes won't help"
- Critic is strict for patches, lenient for docs
- DemandSolver had no `patch_source` action

**Already fixed (committed):**
- DemandSolver now has `patch_source` action delegating to SourcePatcher
- EvolutionAgent Phase 3 prompt rebalanced: "choose the RIGHT fix, not the easiest"
- Critic less hostile to correct-but-imperfect patches
- SourcePatcher uses LLM to find target files (replaces hardcoded keyword dict)
- Evolved tools wired into OS agent via `evolution.tool_deployed` event
- EvolutionAgent runs every 3rd cycle instead of every 5th

#### P3 — Constraint Diversity: Dropout for Agentic OS

**Core idea**: Each container gets different restrictions (like neural network dropout) so evolution learns GENERAL solutions, not environment-specific ones. When deployed to a real user's machine (corporate laptop, Raspberry Pi, cloud VM), the OS already has strategies from containers that simulated similar constraints.

**10 constraint profiles for testing:**

| # | Profile | Mem | Docker | Internet | FS | Simulates |
|---|---------|-----|--------|----------|-----|-----------|
| 1 | corporate_laptop | 512MB | No | No | read-only | Locked-down office PC |
| 2 | cloud_vm | 2GB | Yes | Full | full | AWS/GCP dev instance |
| 3 | airgap_server | 1GB | Yes | No | full | Government/military |
| 4 | raspberry_pi | 256MB | No | Full | full | Home IoT |
| 5 | k8s_pod | 512MB | No | Full | read-only+tmpfs | Enterprise K8s |
| 6 | student_shared | 256MB | No | Full | home-only | University hosting |
| 7 | ci_runner | 1GB | Yes | Full | ephemeral | GitHub Actions |
| 8 | enterprise_proxy | 1GB | No | proxy | full | Corporate with proxy |
| 9 | minimal_server | 512MB | No | Full | full | Old Linux, bare tools |
| 10 | dev_machine | 4GB | Yes | Full | full | Developer workstation |

**Deployment progression:**
1. **Docker** (now) — simulate constraints, fast iteration
2. **Windows** (next) — PowerShell, winget, Windows Services, real constraints
3. **Mac** — Homebrew, launchd, Apple Silicon
4. **Linux** — the natural habitat, but Ubuntu ≠ Alpine ≠ RHEL

**The bridge**: `EnvironmentProbe` detects OS + constraints. Evolved strategies tagged with `env_context` activate only when environment matches. Fleet sync shares strategies across all profiles.

#### P4 — Live Patching Without Restart

**Volume mount** `./agos:/app/agos:ro` — source changes visible instantly in all containers.
**`POST /api/reload`** — hot-reload specific modules via `importlib.reload()`. Running agents keep old code, new agents use new code. No rebuild, no restart, no wasted tokens.
**Host bind mounts** for data (`.opensculpt-fleet/`) — survives `docker volume prune`, visible to Claude Code, can be backed up.

## Lessons from Production: Mine These for Fixes

**IMPORTANT**: Before building any new system, check if OpenHands (the most mature open-source agent platform) already solved it. Their GitHub has 40+ production-hardening PRs. Don't reinvent — adapt.

**MANDATORY**: Before writing ANY new feature (context management, loop detection, token optimization, prompt engineering, agent coordination), search these libraries' GitHub issues and PRs first:

| Library | What to mine | GitHub |
|---------|-------------|--------|
| **OpenHands** | Condensers, stuck detector, sandbox, cost tracking | `All-Hands-AI/OpenHands` |
| **OpenClaw** | 4-layer compaction, memory flush, plugin ContextEngines, keep_first, 196K stars | `openclaw/openclaw` |
| **OpenFang** | SHA256 loop detection, WASM sandbox, 11-step agent lifecycle, Hands, 15.6K stars | `RightNow-AI/openfang` |
| **DSPy** | Prompt compilation, automatic optimization | `stanfordnlp/dspy` |
| **LangChain** | Memory types, tool schema management, agent memory | `langchain-ai/langchain` |
| **CrewAI** | Multi-agent context handoff, inter-agent compression | `crewAIInc/crewAI` |
| **AutoGen/AG2** | Conversation management, teachable agents | `ag2ai/ag2` |
| **SWE-agent** | Compact edit format, context window, cost per task | `SWE-agent/SWE-agent` |
| **LlamaIndex** | Context window optimization, document compression | `run-llama/llama_index` |

### OpenHands Patterns to Adopt (mined from GitHub 2025-2026)

| Pattern | OpenHands Implementation | OpenSculpt Status | Priority |
|---------|------------------------|-----------------|----------|
| **5-pattern stuck detector** | `stuck.py`: identical pairs, error loops, message repeats, A-B-A-B oscillation, condensation storms | `guard.py` has 1 pattern only | P0 |
| **9 pluggable condensers** | `noop`, `observation_masking`, `recent`, `llm`, `amortized`, `llm_attention`, `browser_output`, `rolling`, `token_aware` — all via registry | `session.py` has 1 strategy, not applied to sub-agents | P0 |
| **`keep_first` in condensers** | Always preserve the initial task description when compacting — agents forget their goal without it | Not implemented | P0 |
| **Global iteration limit (500)** | Shared across all agents including delegates. Prevents runaway recursion. | Sub-agents have 25 turns but no global cap across goals | P1 |
| **`think` tool** | Lets agent reason without executing. Counts as progress for loop detection. Prevents "agent stuck because it needs to think but loop detector sees no action" | Not implemented | P1 |
| **Per-goal cost tracking** | PR #5396 fixed double-counting. Cost computed ONCE in `_post_completion()`, cached. Accumulated per conversation. | Token tracking exists but not per-goal, not displayed | P1 |
| **Memory monitor** | PR #6684: soft limit 3.5GB (warn), hard limit 3.8GB (kill). Must use cgroups, not prlimit (deprecated) | GC memory pressure monitor added (psutil) | Done |
| **Sandbox security** | `cap-drop ALL`, `--no-new-privileges`, bind to 127.0.0.1, containers STOPPED not deleted (faster resume) | Basic Docker, no cap-drop | P2 |
| **LLM retry** | 8 attempts, 15s-120s exponential backoff, 2x multiplier | 3 retries with 1s-4s backoff | P2 (increase) |
| **Structured cleanup** | Each cleanup step in own try/except. Reference nullification. State tracking prevents double-close. | GC + phase cleanup added, needs error isolation | P2 |
| **Security analyzer** | Classifies actions as Low/Medium/High risk before execution | Not implemented | P2 |
| **Condenser infinite loop fix** | PR #6795: rolling condenser fought truncation in infinite loop. Fix: reset condenser state after truncation | Haven't hit this yet but will when condensers are added | Watch |

### OpenHands Issues That Hit Us Too

| Their Issue | Our Version | Their Fix |
|------------|-------------|-----------|
| [#7960](https://github.com/All-Hands-AI/OpenHands/issues/7960) Agent burns tokens in logical loop | Sub-agents loop on "Let me try..." | 5-pattern stuck detector |
| [#5355](https://github.com/All-Hands-AI/OpenHands/issues/5355) Loop detection kills real agents | False positives on long-running commands | Interactive mode resets detection after progress |
| [#7305](https://github.com/All-Hands-AI/OpenHands/issues/7305) Expired token → infinite loop | OpenRouter 403 → agents keep retrying | Detect auth errors, stop immediately |
| [#6795](https://github.com/All-Hands-AI/OpenHands/pull/6795) Condenser + truncation infinite loop | Haven't hit yet | Reset condenser state after truncation |
| [PR #6684](https://github.com/All-Hands-AI/OpenHands/pull/6684) Docker OOM kills | Our 68GB Docker + OOM crash | Memory monitor with soft/hard limits |
| [PR #5396](https://github.com/All-Hands-AI/OpenHands/pull/5396) Cost double-counting | Token tracking exists but inaccurate | Compute ONCE, cache, reference cached value |

### Token Optimization Research (bake into kernel)

| Technique | Paper/Source | Savings | Status |
|-----------|-------------|---------|--------|
| Observation masking | JetBrains/NeurIPS 2025 "Complexity Trap" | 50% | **Done** |
| Dynamic tool selection | ITR (arXiv 2602.17046, Feb 2026) | 70%/turn | **Done** |
| Token budget per agent | Production best practice | Prevents runaway | **Done** (50K) |
| Loop detection | OpenHands `stuck.py` | 30-40% | Partial (1 pattern, need 5) |
| Pre-flight hard gates | EnvironmentProbe | 100% of doomed | **Done** |
| Prompt caching (Anthropic) | "Don't Break the Cache" Jan 2026 | 41-80% | Not done |
| Plan caching | Agentic Plan Caching Jun 2025 | 50% | Not done |
| Cost-aware model routing | Production pattern | 40-60% | Not done |
| Evolution sleep tiers | Unix interrupt-driven scheduling | 80% fewer idle calls | **Done** |

### Production Cost Benchmarks (what others pay)

| System | Cost per task | Notes |
|--------|-------------|-------|
| Devin | $2.25/15min | 14% real-world completion rate |
| SWE-agent | $11-24/bug fix | 3.7M-8.1M tokens per issue |
| OpenHands | $3-25/PR | Depends on complexity |
| OpenSculpt target | $1-5/scenario | With all optimizations applied |

## How to Evolve the OS (for Claude Code / developers)

Claude Code is the meta-evolution engine. When the OS can't do something, evolve it:

1. **Check demands**: Read `.opensculpt/demands/` or `curl localhost:8420/api/evolution/demands`
2. **Check constraints**: `curl localhost:8420/api/federation/constraints` — what the OS already learned about this environment
3. **Check resolutions**: `curl localhost:8420/api/federation/resolutions` — past fixes that might apply
4. **Read the relevant agos/ source files** — understand the architecture before changing it
5. **Write your fix**: new tool → `agos/tools/`, new daemon → `agos/daemons/`, bug fix → modify source directly
6. **Run tests**: `python -m pytest tests/ --ignore=tests/test_frontend_playwright.py -q`
7. **Hot-load**: New tools in `.opensculpt/evolved/` get auto-loaded. Source changes need restart.
8. **Record what you learned**: Constraints and resolutions auto-federate via the gossip sync protocol

### Knowledge Federation

- Constraints (environment rules) and resolutions (fix patterns) are shared across OpenSculpt instances via P2P gossip sync
- Only ANONYMIZED knowledge is shared — IPs, paths, credentials are stripped
- Raw code is NOT federated — only knowledge patterns. Each instance's Claude Code writes env-appropriate code.
- Domain filtering: instances only pull knowledge matching their active goals

### Key Files for Evolution

- `agos/knowledge/constraints.py` — Environment constraint store (SSO, proxy, paths, preferences)
- `agos/knowledge/resolutions.py` — Resolution pattern cache (symptom → fix strategy)
- `agos/evolution/demand_solver.py` — LLM-powered demand resolution with resolution cache
- `agos/evolution/sync.py` — P2P gossip sync (now includes constraints + resolutions)
- `agos/evolution/cycle.py` — Main evolution loop (demand-gated: no demands = no token burn)

## Branding

- **Name**: OpenSculpt (capital O, capital H)
- **Tagline**: The Self-Evolving Agentic OS
- **CLI**: `sculpt`
- **Package**: `pip install opensculpt`
- **Env prefix**: `SCULPT_`
- **Workspace**: `.opensculpt/`
- **Internal module path**: `agos/` (kept for import compatibility)


Add at the top of CLAUDE.md under a ## Core Principles section\n\nNEVER over-engineer solutions. Prefer simple, minimal implementations (e.g., .md files over SQLite, flat structures over complex abstractions). Ship the simplest thing that works first.
Add under a ## Verification & Testing section at the top level\n\nNEVER claim success without actually verifying the result end-to-end from the user's perspective. Do not rely on backend logs alone—check UI output, run the actual scenario, and show real evidence. If you cannot verify, say so explicitly.
Add under a ## Architecture Assumptions section\n\nThis project (OpenSculpt/agenticOS) is environment-agnostic. Do NOT assume Docker is the runtime. Do not hardcode Docker-specific values or paths unless explicitly told the target is Docker.
Add under ## Core Principles section\n\nFollow the LLM-native philosophy: solutions should be designed for LLM consumption (markdown files, simple text formats, agentic patterns). Avoid traditional software patterns (ORMs, complex SQL, heavy abstractions) unless explicitly requested.
Add under ## Core Principles section\n\nWhen the user says 'ship today' or gives a time constraint, produce the minimum viable implementation immediately. Do NOT create multi-day plans or research phases unless asked.
Add under a ## OpenSculpt-Specific Rules section\n\nWhen working on the agentic OS, delegate tasks TO the OS agent rather than doing the work yourself. The goal is to make the system autonomous, not to be the system.