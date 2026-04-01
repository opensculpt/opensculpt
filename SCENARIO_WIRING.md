# OpenSculpt — Scenario Wiring Spec

**How the OS gets smarter with every use. Not just what fires — WHY it matters.**

---

## How Evolution Actually Works (Not Arxiv Papers)

Evolution is NOT "scan arxiv → generate code." That's one small part.

Real evolution is: **the OS gets better at doing things because it remembers what worked, what failed, and what it learned.**

```
DAY 1: User says "run sales"
  OS Agent → 25 turns, 200K tokens
  Uses shell("docker pull espocrm") — workaround, no native tool
  Doesn't remember CRM location after conversation ends
  Result: CRM installed but OS forgot everything

DAY 2: User says "show me my leads"
  OS Agent → "What CRM? Where is it?"
  Has to re-discover everything from scratch
  Result: Wasted 50K tokens re-discovering what it already did

DAY 30: After TheLoom + Learner + Skill Docs are wired
  OS Agent → 3 turns, 5K tokens
  TheLoom recalls: "EspoCRM at 172.x.x.x, admin:admin123, 47 leads"
  Skill doc injected: knows the API, endpoints, data model
  Uses docker_exec (evolved from demand signals)
  Result: Instant answer. OS is now an expert on YOUR CRM.
```

That's evolution. The OS gets faster, cheaper, and more accurate because it LEARNED from doing the work.

---

## The 5 Layers of Getting Smarter

Every scenario uses these 5 layers. Each layer builds on the one below.

### Layer 1: Memory (TheLoom + Learner)
**What:** After every interaction, OS writes what it did and what it learned.
**Why:** Without this, the OS has amnesia. Every command starts from zero.
**Example:** "Installed EspoCRM on port 8081" → next time user asks about CRM, OS already knows where it is.

### Layer 2: Skills (Skill Docs + WorkingMemory)
**What:** After completing a task, OS saves a SKILL.md with API endpoints, auth patterns, data models.
**Why:** Skill docs get injected into agent prompts. The agent doesn't need to figure out how to use EspoCRM every time — it already has the manual.
**Example:** `sales_crm.md` contains: "EspoCRM API at /api/v1/, auth via Basic base64, Lead fields: firstName, lastName, accountName, emailAddress, status"

### Layer 3: Automation (DomainDaemons + GoalRunner)
**What:** After setup phases, GoalRunner spawns **DomainDaemons** — LLM-powered background workers that run on schedule.
**Why:** An OS that only works when you talk to it is a chatbot. An OS that monitors your CRM at 3am and alerts you about stale deals — that's an operator.
**How:** DomainDaemons use a two-tier tick: `fast_check()` (cheap HTTP/API/Docker ping, no LLM) gates `smart_tick()` (LLM-powered reasoning, capped at 5 turns / 10K tokens). This keeps costs near zero on quiet days.
**Example:** "sales_lead_checker" DomainDaemon runs every 2 hours: `fast_check` hits CRM API for stale leads → if found, `smart_tick` reasons about which leads to prioritize → writes findings to TheLoom.

### Layer 4: Tool Evolution (DemandCollector + ToolEvolver)
**What:** OS tracks what it struggles with. If it uses shell("docker ...") 10 times, it evolves a native docker tool.
**Why:** Native tools are faster, safer, and give better error messages than shell workarounds.
**Example:** First week: `shell("docker exec espocrm curl ...")`. After evolution: `docker_exec("espocrm", "curl ...")`.

### Layer 5: Domain Intelligence (KnowledgeGraph + Consolidator + Arxiv)
**What:** Over weeks, OS builds a knowledge graph of your business. Consolidator compresses patterns. Arxiv finds techniques for domain-specific improvements.
**Why:** This is where the OS becomes specialized. A sales OS thinks differently than a DevOps OS.
**Example:** After 30 days, KnowledgeGraph knows: "Leads from Web convert 3x better than Cold Call. Companies with >50 employees close faster." Arxiv finds a lead scoring paper. CodeGen builds a scorer. OS now prioritizes leads automatically.

---

## Scenario 1: Sales CRM Operator

**User says:** "Help me run sales for my company"

### First Time (Day 1)

**What happens:**
1. **OS Agent** receives command
2. **WorkingMemory** loads — empty (first time)
3. **TheLoom.recall("sales")** — empty (first time)
4. OS Agent calls **set_goal** → **GoalRunner** creates persistent goal
5. **LLM plans phases:** Install CRM → Create Pipeline → Create Leads → Setup Monitoring
6. GoalRunner ticks, spawns **CRM Installer agent**
7. Agent uses **shell/docker/http** to install EspoCRM
8. **Learner** records everything to TheLoom:
   - Episodic: "Installed EspoCRM container, port 8081, took 45 seconds"
   - Semantic: "EspoCRM API at 172.x.x.x/api/v1/, Basic auth admin:admin123"
   - Graph: EspoCRM→port:8081, EspoCRM→image:espocrm/espocrm, EspoCRM→db:mysql
9. **Skill doc created:** `.opensculpt/skills/sales_crm.md`
10. **DemandCollector** notes: "used shell for docker 8 times" → capability_gap signal
11. **AgentRegistry** shows CRM Installer in dashboard Agents tab
12. Next tick: **Data Agent** spawns, creates leads via API
13. **Learner** records: lead names, companies, pipeline stages
14. **KnowledgeGraph** builds: Lead→Company, Opportunity→Stage
15. Next tick: **GoalRunner** spawns **DomainDaemon** "sales_lead_checker":
    - `check_type: "api_query"` — fast_check hits CRM API for stale leads
    - `smart_tick` uses LLM + skill docs to reason about findings
    - Writes discoveries to TheLoom with `daemon:sales_lead_checker` tag
    - Runs every 2 hours, costs $0 when no stale leads found (fast_check gates LLM)
16. Additional DomainDaemons may spawn: pipeline_reporter, follow_up_nudger

**What the user sees:**
- Dashboard: goal progress, agents spawning/completing, events streaming
- Shell: "CRM installed. 10 leads created. Sales monitoring active."
- Agents tab: CRM Installer (completed), Data Agent (completed)
- Hands tab: goal_runner (running), sales_lead_checker (running, ticking)

### Second Time (Day 2)

**User says:** "Show me my leads"

**What happens — WITH memory:**
1. **WorkingMemory** loads: active goal = "run sales", CRM installed
2. **TheLoom.recall("leads")** → "EspoCRM at 172.x.x.x, admin:admin123, 10 leads"
3. OS Agent already knows where CRM is — no re-discovery
4. **http** tool: GET /api/v1/Lead → returns 10 leads
5. **3 turns, 5K tokens** instead of 25 turns, 200K tokens
6. **Learner** records: "User asked about leads, returned 10 from EspoCRM"

**What happens — WITHOUT memory (today's broken state):**
1. WorkingMemory: empty
2. TheLoom: empty
3. OS Agent: "What CRM? Where?" → spends 15 turns finding docker containers, guessing IPs, trying auth
4. **25 turns, 100K tokens** to do what should take 3 turns

### One Week Later

**What evolved:**
- **ToolEvolver** activated docker_* tools (from demand signals)
- **Skill doc** has been enriched: knows all CRM API endpoints, lead field names, pipeline stages
- **KnowledgeGraph** shows: which leads convert best, which sources produce quality leads
- **DomainDaemon "sales_lead_checker"** has ticked 84 times (every 2h for 7 days):
  - fast_check found stale leads on 12 ticks → smart_tick fired 12 times
  - 72 ticks skipped LLM entirely (fast_check said "no stale leads") → cost $0
  - TheLoom now has 12 `daemon:sales_lead_checker` findings
- **Consolidator** compressed 12 daemon findings into: "Average 2 stale leads per day, mostly from Cold Call source"
- **TheLoom Semantic** contains: "Leads from 'Web Site' source convert 40% of the time"
- **Channels** (if configured): sending daily Slack digest of pipeline changes

**User says:** "Which leads should I focus on?"

OS Agent response is now INTELLIGENT:
- Recalls from TheLoom: conversion patterns, source quality, deal velocity
- Answers: "Focus on Sarah Chen (Web lead, 50+ employee company, 3 days since last contact) and DataVision Inc (requested demo, no follow-up)."
- This answer was IMPOSSIBLE on Day 1. It required weeks of memory accumulation.

---

## Scenario 2: Customer Support Manager

**User says:** "Handle customer support for my business"

### First Time
1. **GoalRunner** plans: Install Helpdesk → Configure Queues → Create Auto-rules → Monitor Tickets
2. **Helpdesk Installer agent** → docker_run(zammad) on port 8082
3. **Learner** → stores Zammad API, endpoints, queue structure
4. **Skill doc:** `support_helpdesk.md`
5. **Data Agent** → creates queues (Technical, Billing, General), priority levels
6. **KnowledgeGraph:** Queue→Priority mapping, Agent→Queue assignment
7. **GoalRunner** spawns **DomainDaemon** "ticket_monitor" (check_type: "api_query", every 30 min)
   - fast_check: hits helpdesk API for overdue tickets
   - smart_tick: reasons about which tickets need escalation, writes to TheLoom

### Evolution Over Time
- **Week 1:** ticket_monitor DomainDaemon checks for overdue tickets. Writes findings to TheLoom: "Technical tickets take 4 hours average. Billing tickets take 1 hour."
- **Week 2:** **Consolidator** compresses patterns: "Peak ticket time: Monday 9am-12pm. Common issues: password reset (30%), billing question (25%), bug report (20%)."
- **Week 3:** **DemandCollector** sees: "OS agent spent 10 turns classifying a ticket." Demand: needs a classifier. **Arxiv Scout** finds NLP classification paper. **CodeGen** builds a ticket classifier. **Sandbox** validates it.
- **Week 4:** **Intent Classifier** now auto-routes tickets. Technical → technical queue. Billing → billing queue. Bug → dev team.
- **Month 2:** **KnowledgeGraph** knows: "Customer X has 5 open tickets in 2 weeks. Churn risk." **Proactive** suggests: "Customer X might be unhappy — should we escalate?"

**This is real evolution.** Not arxiv papers for their own sake — the system identified a bottleneck (ticket classification takes too many turns), found a technique, built a tool, and deployed it. The user's support operation got better without them doing anything.

---

## Scenario 3: Internal Knowledge System

**User says:** "Build a knowledge system from my documents"

### First Time
1. **GoalRunner** plans: Scan Files → Build Index → Create Graph → Enable Search
2. **File Scanner agent** → reads all .md, .pdf, .txt, .py files
3. **TheLoom.remember()** → each file summary goes to Semantic weave
4. **KnowledgeGraph** → extracts entities: people, projects, technologies, relationships
5. **MemoryNotes** → creates Zettelkasten-style linked notes for key concepts

### Evolution Over Time
- **Week 1:** User searches "Project Alpha" → TheLoom.recall() returns 5 relevant files
- **Week 2:** **Consolidator** merges duplicate info: 3 files mention "Project Alpha deadline is March" → one consolidated fact
- **Week 3:** **Ambient Watcher** detects new files added → auto-ingests, updates graph
- **Week 4:** **DemandCollector** sees: "recall returned irrelevant results 8 times." Demand: better search. **Evolution** improves the TF-IDF weighting in SemanticWeave.
- **Month 2:** **KnowledgeGraph** is rich: "Alice works on Project Alpha, which depends on Service Y, which Bob maintains. Alice is on vacation next week." User asks "Who can help with Service Y?" → OS answers "Bob."

**This scenario is where TheLoom and KnowledgeGraph are CRITICAL.** Without them, the OS is just `grep`. With them, it understands relationships.

---

## Scenario 4: DevOps Operator

**User says:** "Set up monitoring and keep my services running"

### First Time
1. **GoalRunner** plans: Install Monitoring → Discover Services → Configure Alerts → Auto-Recovery
2. **docker_run** → Prometheus + Grafana stack
3. **shell/docker_ps** → discovers all running containers
4. **TheLoom** → stores: service→port, service→image, service→health_url
5. **KnowledgeGraph** → service dependency map: Web→API→DB
6. **GoalRunner** spawns **DomainDaemon** "service_monitor" (check_type: "http", every 5 min)
   - fast_check: pings health endpoints (no LLM — costs $0 when services are up)
   - smart_tick: only fires when a service is DOWN — LLM reasons about how to fix it

### Evolution Over Time
- **Week 1:** service_monitor DomainDaemon fast_checks health endpoints. Most ticks skip LLM. Records uptime/downtime to TheLoom when smart_tick fires.
- **Week 2:** Service X crashes. OS auto-restarts via docker_run. **Learner** records: "Service X crashed at 3am, OOM killed, restarted in 12 seconds."
- **Week 3:** **Consolidator** detects pattern: "Service X crashes every 3 days under load."  **DemandCollector:** "service_monitor restarted X 4 times this week." Demand: needs capacity adjustment.
- **Week 4:** **Evolution** generates a memory-limit-adjuster tool from a paper about adaptive resource allocation. Sandbox validates. Next time Service X gets heavy load → OS increases its memory limit before it crashes.
- **Month 2:** OS knows: "Deploy on Tuesday is risky (3 out of 4 Tuesday deploys caused incidents). Wednesday is safest." **Proactive** suggests: "You're about to deploy on Tuesday — historical data shows 75% incident rate. Consider Wednesday."

---

## Scenario 5: Company-in-a-Box

**User says:** "Run operations for my startup"

### First Time
1. **GoalRunner** plans: Install CRM + Helpdesk + Wiki + Monitoring
2. **Spawns 4 agents in parallel** — each installs one system
3. **Coordination/Team** manages: assigns ports (8081, 8082, 8083, 3000), prevents conflicts
4. **TheLoom** stores ALL system locations, credentials, APIs
5. **KnowledgeGraph** maps cross-system relationships
6. GoalRunner spawns 4 **DomainDaemons** (each gets ALL skill docs via `skill_paths: list`):
   - sales_lead_checker (check_type: api_query, every 2h)
   - ticket_monitor (check_type: api_query, every 30min)
   - wiki_watcher (check_type: always, daily) — checks for new content
   - service_monitor (check_type: http, every 5min) — pings all systems

### Evolution Over Time
- **Week 1:** Systems run independently. Each monitor hand does its own checks.
- **Week 2:** **KnowledgeGraph** connects data: "Lead Sarah Chen has open support ticket #105." **Proactive** suggests: "Sales should know about this before their follow-up call."
- **Week 3:** **Triggers** established: CRM lead status change → webhook → create/update helpdesk contact. Cross-system data flow is automated.
- **Week 4:** **Coordination** manages conflicts: "CRM database migration running — pause helpdesk sync to prevent data corruption."
- **Month 2:** **Digest Hand** sends unified daily report: "3 new leads, 2 deals closed ($50K), 5 tickets resolved, all services healthy, wiki updated 12 times."
- **Month 3:** **A2A Protocol** connects to partner company's OpenSculpt instance. Shared customer data flows between CRM systems (with policy approval).
- **Month 6:** **P2P Sync** shares evolved patterns with fleet. Other startups get: "EspoCRM+Zammad integration connector" that YOUR instance evolved. They contribute back: "Grafana alert template for CRM downtime."

---

## What "Evolve" ACTUALLY Means Per Layer

| Layer | Day 1 | Week 1 | Month 1 | Month 6 |
|-------|-------|--------|---------|---------|
| **Memory** | Empty | OS remembers CRM location, leads | Full interaction history, patterns | Years of business knowledge |
| **Skills** | No skill docs | CRM API documented | All system APIs, auth, data models | Deep domain expertise |
| **Automation** | No background tasks | Hourly CRM check | Cross-system monitoring | Self-coordinating agent fleet |
| **Tools** | shell workarounds | docker_* activated | Custom domain tools evolved | Business-specific tools (lead scorer, ticket classifier) |
| **Intelligence** | Generic Claude | Knows your systems | Knows your business patterns | Predicts problems, suggests strategy |

---

## Component Purpose — Why Each Exists

| Component | Plain English | Without It |
|-----------|-------------|------------|
| **TheLoom Episodic** | Diary — what happened and when | OS forgets everything after each conversation |
| **TheLoom Semantic** | Fact sheet — searchable knowledge | OS can't answer "where is my CRM?" |
| **KnowledgeGraph** | Relationship map — who/what connects to what | OS can't answer "which leads have open tickets?" |
| **Learner** | Auto-journalist — writes to diary + fact sheet after every action | TheLoom stays empty forever |
| **Consolidator** | Librarian — compresses old diary entries into useful patterns | TheLoom fills up with noise, no insight |
| **WorkingMemory** | Desk — what you're working on right now | OS has no context for current task |
| **MemoryNotes** | Index cards — linked concepts | Can't connect "Project Alpha" → "Alice" → "deadline March" |
| **Skill Docs** | Instruction manual — how to use each system | OS re-discovers APIs every single time |
| **SessionCompactor** | Summarizer — keeps conversations manageable | Long conversations overflow context window |
| **GoalRunner** | Project manager — breaks big asks into phases | OS can only do one thing at a time |
| **TaskPlanner** | Checklist — checkpointed multi-step tasks | 20-step tasks have no recovery if step 15 fails |
| **CapabilityGate** | Security guard — limits what each agent can do | Researcher agent can delete files |
| **LoopGuard** | Circuit breaker — stops infinite loops | OS burns 500K tokens going in circles |
| **DemandCollector** | Complaint box — tracks what the OS can't do | Evolution has no direction, generates random code |
| **ToolEvolver** | Toolsmith — builds new tools from demand | OS never gains new capabilities |
| **Arxiv Scout** | Research assistant — finds techniques from papers | Evolution has no ideas |
| **MetaEvolver** | Self-tuner — adjusts evolution parameters | Evolution either does too much or too little |
| **DomainDaemon** | Domain expert worker — LLM-powered background task with fast_check gating | GoalRunner creates dumb cron jobs instead of intelligent workers |
| **Hands** | Employees — background workers that run 24/7 | OS only works when user talks to it |
| **Scheduler** | Calendar — runs things on time | No proactive monitoring |
| **Channels** | Messenger — sends alerts to Slack/email | User has to check dashboard manually |
| **Triggers** | Sensor — watches for file changes, webhooks, schedules | OS misses external events |
| **Coordination** | Team lead — manages multi-agent work | Agents conflict, overwrite each other |
| **Intent Classifier** | Receptionist — understands what user wants | OS guesses wrong, wastes turns |
| **Proactive** | Advisor — suggests actions before asked | OS is purely reactive |
| **MCP** | Extension cord — connects external tool servers | Limited to built-in tools only |
| **A2A** | Diplomat — talks to other agent systems | Isolated, can't collaborate |
| **P2P Sync** | Teacher — shares learnings with other nodes | Every node learns alone |

---

## Priority Wiring Order

Wire in this order — each level enables the next:

### Level 1: Memory (makes the OS remember)
- [x] Learner instantiated and called after every OS Agent execute()
- [x] Learner called after every sub-agent completion
- [x] TheLoom.remember() for docker_run, http 200 results
- [x] KnowledgeGraph.link() for installed systems, credentials, ports
- [x] WorkingMemory loaded at start of each command

### Level 2: Skills (makes the OS expert)
- [x] Skill docs created after each GoalRunner phase
- [x] Skill docs injected into spawned agent prompts
- [x] TheLoom.recall() used by sub-agents AND DomainDaemons (not just OS agent)

### Level 3: Persistence (makes agents useful)
- [x] GoalRunner spawns DomainDaemons (LLM-powered, not dumb cron)
- [x] DomainDaemons have configurable interval + check_type
- [x] DomainDaemons visible in dashboard Hands tab with live status
- [x] DomainDaemon results saved to TheLoom (tagged `daemon:{name}`)

### Level 4: Automation (makes the OS proactive)
- [x] GoalRunner creates DomainDaemons for monitoring phases (replaces old scheduler approach)
- [x] DomainDaemon fast_check gates LLM (http/api_query/docker/always check_types)
- [ ] Channels wired for Slack/email alerts
- [ ] Triggers wired for webhooks and file changes
- [ ] Digest hand includes DomainDaemon findings from TheLoom

### Level 5: Evolution (makes the OS grow)
- [x] DemandCollector signals collected from shell workarounds + tool failures
- [x] Docker/browser tools activated at boot (serve.py)
- [x] Consolidator runs hourly in background (boot.py _consolidation_loop)
- [ ] Arxiv finds domain-relevant papers (not random CS papers)
- [ ] EvolutionMemory tracks what worked across cycles

### Level 6: Intelligence (makes the OS smart)
- [x] Intent Classifier wired to OS agent (enriches context, doesn't replace loop)
- [ ] Proactive suggestions based on TheLoom patterns
- [ ] Coordination manages multi-agent work
- [ ] KnowledgeGraph queries power cross-system insights

### Level 7: Scale (makes the OS a platform)
- [x] MCP connects external tool servers (wired in serve.py)
- [x] A2A talks to other agent systems (wired in serve.py)
- [ ] P2P Sync shares evolved patterns across fleet

---

## Test Scenarios — Proving Memory and Evolution Work

These are NOT feature tests. These are conversations that MUST produce different results on Day 1 vs Day 7. If the OS gives the same answer both times, memory is broken.

### Test 1: Memory — Does the OS remember what it installed?

```
SESSION 1:
  User: "install a CRM for my sales team"
  Expected: OS spawns sub-agent → installs EspoCRM → saves to TheLoom
  Verify: TheLoom has entry: "EspoCRM at port 8081, admin:admin123"
  Verify: Skill doc exists: .opensculpt/skills/sales_crm.md

  [RESTART SERVER]

SESSION 2:
  User: "where is my CRM?"
  Expected: OS recalls from TheLoom → "EspoCRM at port 8081"
  FAIL if: OS says "I don't know" or tries to discover it again
  Verify: took <5 turns (not 25)
```

### Test 2: Memory — Does the OS remember leads?

```
SESSION 1:
  User: "create 5 leads in my CRM"
  Expected: Sub-agent loads Skill → knows API → creates leads → saves to TheLoom
  Verify: TheLoom semantic has lead names and companies

SESSION 2:
  User: "show me my leads"
  Expected: OS recalls CRM location from TheLoom → queries API → shows leads
  FAIL if: OS asks "what CRM?" or re-discovers the API
```

### Test 3: Memory — Do sub-agents inherit knowledge?

```
SESSION 1:
  User: "install CRM and helpdesk"
  Expected: Two sub-agents spawn. CRM installer finishes first.
  Verify: Skill doc for CRM saved before helpdesk agent starts.

SESSION 2:
  User: "connect my CRM to my helpdesk — sync customer data"
  Expected: OS recalls BOTH system locations from TheLoom
  Expected: Sub-agent gets BOTH skill docs injected
  FAIL if: Sub-agent re-discovers either system
```

### Test 4: Skills — Do skills make agents faster?

```
SESSION 1 (no skills):
  User: "check my CRM for stale leads"
  Measure: turns taken, tokens used
  Expected: ~15 turns, ~100K tokens (agent discovers API from scratch)

SESSION 2 (after skill doc exists):
  User: "check my CRM for stale leads"
  Measure: turns taken, tokens used
  Expected: ~3 turns, ~10K tokens (agent loads skill, knows API)
  FAIL if: tokens not reduced by at least 50%
```

### Test 5: Evolution — Does the OS evolve tools from demand?

```
SESSION 1:
  User: "install nginx using docker"
  Expected: OS uses shell("docker run nginx") — no native docker tool yet
  Verify: DemandCollector has signal: "capability_gap: docker"
  Verify: /api/evolution/demands shows "missing_tool: docker"

  [WAIT for evolution cycle — 30 seconds]

  Verify: evolution log shows "Builtin tool 'docker' activated"
  Verify: OS agent now has docker_run in its tool list

SESSION 2:
  User: "install redis using docker"
  Expected: OS uses docker_run("redis") — native tool, not shell
  Verify: audit trail shows tool_name="docker_run" not "shell"
  FAIL if: OS still uses shell("docker run redis")
```

### Test 6: Evolution — Does the OS evolve from repeated failures?

```
SESSION 1-5:
  User asks 5 different questions about Docker containers
  OS uses shell("docker ps"), shell("docker logs"), etc. each time
  DemandCollector accumulates: "docker" capability gap, count=5, priority escalating

  [WAIT for evolution cycle]

  Verify: all docker_* tools activated
  Verify: EvolutionMemory records: "activated docker tools from 5x demand"

SESSION 6:
  User: "show me container logs"
  Expected: OS uses docker_logs("container") — not shell
```

### Test 7: DomainDaemon — Does the monitor keep running?

```
SESSION 1:
  User: "run sales for my company"
  Expected: GoalRunner creates goal → phases execute → DomainDaemon "sales_lead_checker" spawned
  Verify: /api/daemons shows "sales_lead_checker" with status RUNNING
  Verify: check_type is "api_query" (auto-detected from task description)
  Verify: skill_paths includes sales skill docs

  [WAIT 2 hours]

  Verify: sales_lead_checker has ticked multiple times
  Verify: Most ticks had fast_check skip LLM (tokens_used=0)
  Verify: TheLoom has entries tagged "daemon:sales_lead_checker" from smart_tick runs
  Verify: No user intervention needed — it ran autonomously

  [RESTART SERVER]

  Verify: GoalRunner reloads goals from disk
  FAIL if: DomainDaemons not re-created (NOTE: daemon persistence across restart not yet implemented)
```

### Test 8: Knowledge Graph — Cross-system intelligence

```
SESSION 1:
  User: "install CRM and helpdesk"
  Both installed. KnowledgeGraph has:
    CRM→port:8081, Helpdesk→port:8082

SESSION 2:
  User: "create lead Sarah Chen in CRM and a ticket for her in helpdesk"
  KnowledgeGraph should link:
    Lead:Sarah Chen → CRM:EspoCRM
    Ticket:#101 → Helpdesk:Zammad
    Lead:Sarah Chen → Ticket:#101

SESSION 3:
  User: "does Sarah Chen have any open tickets?"
  Expected: KnowledgeGraph traversal → finds Ticket:#101 → checks status
  FAIL if: OS searches both systems from scratch instead of using graph
```

### Test 9: Consolidator — Pattern detection over time

```
SESSIONS 1-30 (simulated — sales_monitor runs 30 times):
  Each tick records: "checked leads, X stale, Y from source Z"

  [RUN Consolidator]

  Verify: Episodic has 30 raw entries
  Verify: Semantic has NEW consolidated entry: "Average 2 stale leads/day, Cold Call source has 70% stale rate"
  Verify: Old episodic entries pruned or compressed

SESSION 31:
  User: "what's the pattern with stale leads?"
  Expected: OS recalls consolidated pattern, not 30 raw entries
  Expected: "Cold Call leads go stale 3x more than Web leads"
```

### Test 10: Full Scenario — Day 1 to Day 30

```
DAY 1:
  User: "run sales for my company"
  Measure: total tokens, total turns, total time
  Result: CRM installed, leads created, DomainDaemon "sales_lead_checker" spawned
  Skills: 0 → 1 (sales_crm.md)
  TheLoom: 0 → ~20 entries
  DomainDaemons: sales_lead_checker (check_type: api_query, every 2h)
  Tools: docker_* activated at boot

DAY 7:
  User: "how are my leads doing?"
  Measure: tokens (should be <20% of Day 1)
  Result: Instant answer from TheLoom + API call + DomainDaemon findings
  Skills: enriched with lead field patterns
  TheLoom: ~30 entries (20 from setup + ~12 DomainDaemon findings from smart_ticks)
  DomainDaemon: ticked 84 times, fast_check skipped LLM 72 times (cost ≈ $0)

DAY 14:
  User: "which leads should I focus on?"
  Result: Intelligent answer based on 14 days of patterns
  TheLoom: consolidated patterns about conversion rates
  KnowledgeGraph: Lead→Company→Deal→Stage relationships

DAY 30:
  User: "give me a sales report"
  Result: Full report with trends, predictions, recommendations
  Memory: months of accumulated knowledge from DomainDaemon findings
  Evolution: custom lead scoring tool evolved from arxiv paper
  DomainDaemon: ticked 360 times, smart_tick fired ~60 times, caught 45 stale leads

  VERIFY: Day 30 response is fundamentally different from Day 1
  VERIFY: Token usage per command decreased by >80%
  VERIFY: OS makes suggestions the user didn't ask for (proactive)
  FAIL if: Day 30 feels the same as Day 1
```
