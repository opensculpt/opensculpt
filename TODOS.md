# OpenSculpt — Deferred Work

Collected from /plan-ceo-review and /plan-eng-review on 2026-04-05.
Items are prioritized P1 (do after golden demo) through P3 (nice to have).

## P1 — Do After Golden Demo Proves Thesis

### Race condition: GoalRunner retry timing
**What:** GoalRunner should delay retry by 1 tick after receiving `evolution.tool_deployed` to let OS agent load the tool first.
**Why:** Without delay, GoalRunner might retry with old tools if OS agent hasn't finished loading. Race condition between two EventBus subscribers.
**Context:** `goal_runner.py` retry logic at line 371-378. The `evolution.tool_deployed` handler needs to set a flag, and retry should happen on the *next* tick, not immediately. ~5 LOC fix.
**Effort:** S (CC: ~5 min)
**Depends on:** GoalRunner retry-on-evolution wiring (in scope for PR2)

### Write 4 critical tests for golden demo
**What:** (1) Unit test for demand_to_queries LLM conversion, (2) Unit test for GitHub Code Search with mocked API, (3) Integration test for GoalRunner retry-on-evolution, (4) E2E Playwright test for full evolution cycle.
**Why:** Test coverage for the golden demo code paths is 5% (1/19). The demo will be flaky without automated regression tests.
**Context:** No existing tests cover the evolution E2E pipeline. The rehearsal step (5 manual runs) is the only current validation. These 4 tests cover 80% of the risk.
**Effort:** M (CC: ~1 hour)
**Depends on:** PR2 golden demo implementation

### GitHub search result caching
**What:** Cache GitHub Code Search results in `.opensculpt/research_cache/` keyed by query hash. TTL: 24 hours.
**Why:** GitHub API rate limit is 30 req/min authenticated. Without caching, repeated demands for the same capability gap hit GitHub every evolution cycle.
**Context:** Outside voice recommended this. User declined for initial demo but it's needed for the live instance (Phase 3). ~20 LOC.
**Effort:** S (CC: ~15 min)
**Depends on:** Research-informed evolution feature

### A/B measurement: research-informed vs self-generated
**What:** Log `research_informed: true/false` in evolution trace. Compare sandbox pass rates and deploy success rates between the two paths.
**Why:** Without measurement, you can't prove the research feature actually helps. The 0/14 success rate might improve from verification fixes, not from research.
**Context:** Outside voice recommended this. ~10 LOC to log, analysis can be done via grep on evolution logs.
**Effort:** S (CC: ~10 min)
**Depends on:** Research-informed evolution feature

## P2 — UX Improvements

### Confidence meters replacing pass/fail
**What:** Replace binary green/red ticks with confidence bars: "Deploy CRM: 40% confident (first attempt)" → "85% confident (6/7 across fleet)".
**Why:** Fixes P0 bug where OS lies about results (green ticks on failures). Makes evolution progress visible to users.
**Context:** From CEO review expansion #3. Requires changing goal status model from boolean to float. Dashboard rendering change.
**Effort:** M (CC: ~2 hours)
**Depends on:** Dashboard evolution panel

### "Why did I fail?" causal chain visualization
**What:** On any failed goal, show the full chain: "Failed because → verify used Docker → env has no Docker → env probe not injected → demand #47 filed."
**Why:** Makes the OS's reasoning transparent and debuggable. Useful for the live instance where viewers need to understand WHY evolution triggers.
**Context:** From CEO review expansion #4. Data already exists in demand signals and goal phases. Need a UI to render the chain.
**Effort:** S (CC: ~45 min)

### Auto-generated evolution changelog
**What:** Human-readable CHANGELOG documenting every real capability the OS gained: "v0.1.7: Learned to deploy web apps via apt-get when Docker unavailable."
**Why:** Proof artifact for README. "Don't take our word for it — here's what the OS taught itself."
**Context:** From CEO review expansion #5. Evolution trace already has the data. Need a formatter.
**Effort:** S (CC: ~30 min)

### Move DemandSolver prompt to template
**What:** Extract the DemandSolver LLM prompt (~50 lines) from demand_solver.py into `.opensculpt/prompts/demand_solver.md`.
**Why:** Following LLM-native principle. Makes the prompt editable without code changes. Evolution engine could potentially improve its own prompts.
**Context:** From eng review code quality finding. The prompt mixes action definitions, JSON schemas, and decision rules in one string.
**Effort:** S (CC: ~15 min)

## P3 — Future Vision

### "Teach me" mode
**What:** Let users proactively teach the OS: "When you see error X, try Y first." Stored as a resolution, shared across fleet.
**Why:** Human-in-the-loop accelerator for evolution. Instead of waiting for failures, users inject knowledge.
**Context:** From CEO review expansion #6. Needs UX design for the teaching interface.
**Effort:** M (CC: ~2 hours)

### Evolution forking (git branches for learning)
**What:** Let users create "branches" of the evolution engine to try different approaches and compare results.
**Why:** Like git branches but for the OS's learning path. Enables experimentation.
**Context:** From CEO review expansion #7. Complex state management. Probably requires a proper versioning system for evolution state.
**Effort:** XL (CC: ~3 days)
