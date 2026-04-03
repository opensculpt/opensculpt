"""DemandSolver -- LLM-powered evolution orchestrator.

Inspired by Voyager (executable skill library), Reflexion (failure reflections),
Live-SWE-agent (runtime tool creation), and RepairAgent (hypothesis-driven fixes).

Core principle: produce EXECUTABLE ARTIFACTS, not vague principles.
- create_tool: generate a Python tool, sandbox-test it, deploy it
- create_skill_doc: write a .opensculpt/skills/ doc with concrete API patterns
- patch_source: delegate to SourcePatcher for code fixes
- research_and_fix: search web for solutions, then generate fix
- tell_user: escalate after 3+ failed attempts

Every action is verified before storage (Voyager pattern).
Failed attempts produce specific reflections (Reflexion pattern).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agos.events.bus import EventBus
from agos.policy.audit import AuditTrail
from agos.evolution.demand import DemandCollector, DemandSignal
from agos.evolution.state import EvolutionMemory, EvolutionInsight

_logger = logging.getLogger(__name__)

# Cache web research results to avoid re-searching
_research_cache: dict[str, str] = {}


class DemandSolver:
    """Orchestrates evolution to solve real demands with executable artifacts."""

    def __init__(
        self,
        event_bus: EventBus,
        audit: AuditTrail,
        demand_collector: DemandCollector,
        evo_memory: EvolutionMemory | None = None,
    ):
        self._bus = event_bus
        self._audit = audit
        self._demands = demand_collector
        self._memory = evo_memory
        self._cycle = 0

    async def tick(self, llm=None, source_patcher=None, tool_evolver=None) -> dict:
        """One demand-solving cycle. Produces executable fixes, not wisdom.

        Returns {"solved": N, "principles": N, "tools_created": N, "told_user": N}
        """
        self._cycle += 1
        result = {"solved": 0, "principles": 0, "tools_created": 0,
                  "patched": 0, "told_user": 0, "skipped": 0}

        demands = self._demands.top_demands(limit=2)
        if not demands or not llm:
            return result

        # Gather context (cheap -- no LLM calls)
        try:
            from agos.environment import EnvironmentProbe
            env_summary = EnvironmentProbe.summary()
        except Exception:
            env_summary = "Unknown environment"

        memory_context = ""
        if self._memory:
            memory_context = self._memory.context_prompt("demand_solving")

        try:
            from agos.evolution.codebase_map import read_codebase_map
            codebase = read_codebase_map()
        except Exception:
            codebase = "(unavailable)"

        for demand in demands:
            # Check past attempts and build failure chain
            failure_chain = ""
            if self._memory:
                past = [i for i in self._memory.insights
                        if demand.description[:40] in (i.what_tried or "")]
                if len(past) >= 5:
                    result["skipped"] += 1
                    demand.mark_attempt()
                    continue
                # OpenSeed lesson: after 3 symptom patches, provide the full
                # failure chain so the LLM can reason about root cause.
                # Data changes behavior; prompt tone doesn't.
                if len(past) >= 3:
                    chain_lines = []
                    for p in past:
                        chain_lines.append(
                            f"  Attempt: {p.what_tried[:60]}\n"
                            f"  Outcome: {p.outcome}\n"
                            f"  Reason: {p.reason[:100]}\n"
                            f"  What worked: {p.what_worked[:80] if p.what_worked else 'nothing'}"
                        )
                    failure_chain = (
                        f"\nFAILURE CHAIN ({len(past)} prior attempts — "
                        f"all failed to resolve this demand):\n"
                        + "\n---\n".join(chain_lines)
                    )
                    _logger.info(
                        "DemandSolver: root-cause mode for '%s' (attempt %d)",
                        demand.description[:40], len(past),
                    )

            # ── FAST PATH: Fingerprint-based resolution lookup (no LLM cost) ──
            try:
                from agos.knowledge.tagged_store import TaggedResolutionStore
                _rs = TaggedResolutionStore()
                _fix = _rs.lookup(demand.description)
                if _fix:
                    _logger.info("Resolution hit for '%s' → %s", demand.description[:40], _fix[:40])
                    result["solved"] += 1
                    demand.mark_attempt()
                    continue
            except Exception:
                pass

            try:
                # Research if this demand has failed before
                research = ""
                if demand.attempts > 0 and demand.count >= 2:
                    research = await self._research(demand)

                action_result = await self._diagnose_and_act(
                    demand, env_summary, memory_context, codebase,
                    llm, source_patcher, tool_evolver, research,
                    failure_chain=failure_chain,
                )
                if action_result == "solved":
                    result["solved"] += 1
                elif action_result == "tool_created":
                    result["tools_created"] += 1
                elif action_result == "principle":
                    result["principles"] += 1
                elif action_result == "told_user":
                    result["told_user"] += 1
                else:
                    result["skipped"] += 1
            except Exception as e:
                _logger.warning("DemandSolver error for '%s': %s",
                                demand.description[:40], e)
                # Record failure reflection (Reflexion pattern)
                if self._memory:
                    self._memory.record(EvolutionInsight(
                        cycle=self._cycle,
                        what_tried=f"Failed: {demand.description[:50]}",
                        module=demand.source,
                        outcome="error",
                        reason=str(e)[:200],
                    ))
            finally:
                # ALWAYS mark attempt so backoff works
                demand.mark_attempt()

        if any(result[k] for k in ("solved", "principles", "tools_created")):
            await self._bus.emit("evolution.demand_solver_tick", {
                "cycle": self._cycle, **result,
            }, source="demand_solver")

        return result

    async def _record_resolution(self, demand: DemandSignal, fix_strategy: str, root_cause: str = "") -> None:
        """Add resolution to tagged resolution store (fingerprint-deduped)."""
        try:
            from agos.knowledge.tagged_store import TaggedResolutionStore
            _rs = TaggedResolutionStore()
            _rs.add(
                symptom=demand.description[:80],
                fix=fix_strategy,
                root_cause=root_cause or demand.kind,
                source=demand.source,
            )
        except Exception as e:
            _logger.debug("Failed to record resolution: %s", e)

    async def _diagnose_and_act(
        self, demand: DemandSignal, env: str, memory: str,
        codebase: str, llm, source_patcher, tool_evolver, research: str,
        failure_chain: str = "",
    ) -> str:
        """One LLM call to diagnose + generate executable fix."""
        from agos.llm.base import LLMMessage

        # Build past reflections for this demand (Reflexion pattern)
        reflections = ""
        if self._memory:
            past = [i for i in self._memory.insights
                    if demand.description[:40] in (i.what_tried or "")]
            if past:
                lines = []
                for p in past[-3:]:
                    lines.append(f"- Tried: {p.what_tried[:60]} -> {p.outcome}: {p.reason[:80]}")
                reflections = "\n".join(lines)

        # Clean demand description — strip docker format strings that confuse LLM
        clean_desc = re.sub(r'\{\{[^}]*\}\}', '', demand.description)[:250]

        # Build prompt parts
        past_section = f"\nPAST ATTEMPTS:\n{reflections}" if reflections else ""
        research_section = f"\nRESEARCH:\n{research[:400]}" if research else ""
        # OpenSeed lesson: after 3+ failed attempts, inject the full failure
        # chain so the LLM sees the PATTERN of failures, not just the latest one.
        chain_section = f"\n{failure_chain}" if failure_chain else ""

        # Load evolution patterns skill (research-backed decision framework)
        skill_context = ""
        try:
            patterns_path = Path(".opensculpt/skills/evolution_patterns.md")
            if patterns_path.exists():
                skill_context = patterns_path.read_text(encoding="utf-8")[:800]
        except Exception:
            pass

        # Classify failure type (from evolution_patterns.md research)
        failure_type = self._classify_failure(demand)

        system_msg = (
            "You are a self-healing engine. You output ONLY valid JSON with double quotes. "
            "No markdown, no explanation, no code blocks. Just one JSON object.\n\n"
            "CONSENT TIERS — choose the right level:\n"
            "- autonomous: skill docs, prompt tweaks (low impact, just do it)\n"
            "- notify: new tools, behavioral changes (medium impact, log it)\n"
            "- approve: code patches to core modules (high impact, show plan first)\n"
            "- tell_user: environmental issues, architecture problems (needs human)\n"
        )

        prompt = (
            f"Problem: {demand.kind} from {demand.source}\n"
            f"Detail: {clean_desc}\n"
            f"Occurred {demand.count}x, priority {demand.priority:.1f}\n"
            f"Failure type: {failure_type}\n\n"
            f"Environment: {env[:300]}\n"
            f"{past_section}{research_section}{chain_section}\n\n"
        )

        if skill_context:
            prompt += f"EVOLUTION PATTERNS (follow these):\n{skill_context}\n\n"

        prompt += (
            "Pick ONE action and return JSON:\n\n"
            '1. create_skill_doc — write a how-to for future agents (autonomous):\n'
            '   {"action":"create_skill_doc","topic":"NAME","content":"markdown how-to text"}\n\n'
            '2. create_tool — Python function to fix a missing capability (notify):\n'
            '   {"action":"create_tool","name":"TOOL","code":"async def handler(x): ...","description":"what it does"}\n\n'
            '3. patch_source — fix a bug in existing OS code, auto-tested + auto-rollback (approve):\n'
            '   {"action":"patch_source","file":"agos/path/to/file.py","description":"what to fix"}\n'
            '   Use when the problem is a code bug (crash, missing retry, wrong logic).\n\n'
            '4. tell_user — environmental issues or things you cannot fix:\n'
            '   {"action":"tell_user","message":"what you need from the user"}\n\n'
            '5. clear — demand is stale or transient:\n'
            '   {"action":"clear","reason":"why"}\n\n'
            "RULES:\n"
            "- Transient failures (timeout, rate limit) → clear with reason\n"
            "- Environmental (missing software, wrong OS) → tell_user\n"
            "- Code bugs → patch_source\n"
            "- Missing capability → create_tool\n"
            "- If docs/tools haven't worked after 2+ attempts, try patch_source.\n"
            "Output ONLY the JSON object."
        )

        text = ""
        try:
            resp = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=system_msg),
                    LLMMessage(role="user", content=prompt),
                ],
                max_tokens=500,
            )
            text = (resp.content or "").strip()
            if not text:
                return "skip"

            # Extract JSON from markdown code blocks if present
            if "```" in text:
                parts = text.split("```")
                for part in parts[1:]:
                    cleaned = part.strip()
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:].strip()
                    if cleaned.startswith("{"):
                        text = cleaned.split("```")[0].strip() if "```" in cleaned else cleaned
                        break

            # Try to find JSON object — must contain "action" key
            for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text):
                candidate = match.group()
                if '"action"' in candidate or "'action'" in candidate:
                    text = candidate
                    break

            # Fix common LLM JSON issues: single quotes, trailing commas, newlines in strings
            try:
                action = json.loads(text)
            except json.JSONDecodeError:
                # Step 1: Replace single-quoted keys/values with double quotes
                # Match 'key': 'value' patterns
                fixed = re.sub(r"'(\w+)'\s*:", r'"\1":', text)
                fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
                # Remove trailing commas before } or ]
                fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
                # Escape unescaped newlines inside strings
                fixed = re.sub(r'(?<=": ")(.*?)(?=")', lambda m: m.group(0).replace('\n', '\\n'), fixed)
                try:
                    action = json.loads(fixed)
                except json.JSONDecodeError:
                    # Last resort: extract action type with regex
                    m = re.search(r'"action"\s*:\s*"(\w+)"', text)
                    if m:
                        action = {"action": m.group(1)}
                        # Try extracting other fields
                        for field in ("name", "topic", "message", "reason", "principle"):
                            fm = re.search(rf'"{field}"\s*:\s*"([^"]*)"', text)
                            if fm:
                                action[field] = fm.group(1)
                        # Extract code/content blocks
                        for field in ("code", "content"):
                            fm = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
                            if fm:
                                action[field] = fm.group(1).replace('\\n', '\n').replace('\\"', '"')
                    else:
                        raise
        except Exception as e:
            _logger.info("DemandSolver: LLM response parse failed: %s | raw: %s", str(e)[:60], text[:120])
            return "skip"

        action_type = action.get("action", "skip")

        # ── ACTION: create_tool ──
        if action_type == "create_tool":
            return await self._handle_create_tool(demand, action, env, tool_evolver)

        # ── ACTION: create_skill_doc ──
        elif action_type == "create_skill_doc":
            return await self._handle_create_skill_doc(demand, action, env)

        # ── ACTION: patch_source ──
        elif action_type == "patch_source":
            return await self._handle_patch_source(demand, action, source_patcher)

        # ── ACTION: tell_user — refresh context + nudge user with tool-specific instructions ──
        elif action_type == "tell_user":
            return await self._try_vibe_tool(demand, env)

        # ── ACTION: clear ──
        elif action_type == "clear":
            key = f"{demand.kind}:{demand.source}"
            self._demands.clear_resolved(key)
            if self._memory:
                self._memory.record(EvolutionInsight(
                    cycle=self._cycle,
                    what_tried=f"Cleared: {demand.description[:50]}",
                    module=demand.source,
                    outcome="success",
                    reason=action.get("reason", "stale"),
                    what_worked=f"Cleared stale demand: {action.get('reason', '')[:60]}",
                ))
            _logger.info("DemandSolver: cleared demand -- %s", action.get("reason", "")[:60])
            return "solved"

        return "skip"

    async def _handle_patch_source(self, demand, action, source_patcher) -> str:
        """Delegate source patching to SourcePatcher pipeline."""
        if not source_patcher:
            _logger.info("DemandSolver: patch_source requested but no source_patcher")
            return "skip"
        file_path = action.get("file", "")
        if not file_path:
            return "skip"

        opportunity = {
            "signal": demand,
            "file_path": file_path,
            "error_context": action.get("description", demand.description[:200]),
            "count": demand.count,
            "priority": demand.priority,
            "demand_key": f"{demand.kind}:{demand.source}",
        }
        try:
            patch = await source_patcher.propose(opportunity)
            if not patch:
                _logger.info("DemandSolver: patch_source propose failed for %s", file_path)
                return "skip"
            await source_patcher.snapshot(patch)
            applied = await source_patcher.apply(patch)
            if applied and await source_patcher.health_check(patch):
                source_patcher._pending_verifications.append(patch)
                key = f"{demand.kind}:{demand.source}"
                self._demands.clear_resolved(key)
                if self._memory:
                    self._memory.record(EvolutionInsight(
                        cycle=self._cycle,
                        what_tried=f"patch_source:{file_path}",
                        module=demand.source,
                        outcome="success",
                        reason=f"Patched {file_path}: {patch.rationale[:80]}",
                        what_worked=f"Source patch: {patch.rationale[:80]}",
                    ))
                _logger.info("DemandSolver: patched %s — %s", file_path, patch.rationale[:60])
                await self._record_resolution(demand, f"patch_source:{file_path} — {patch.rationale[:80]}", root_cause=f"code_bug_in_{file_path}")
                return "solved"
            else:
                if applied:
                    await source_patcher.rollback(patch)
                return "skip"
        except Exception as e:
            _logger.warning("DemandSolver: patch_source error: %s", e)
            return "skip"

    async def _handle_create_tool(self, demand, action, env, tool_evolver) -> str:
        """Create a tool, sandbox-test it, deploy it. Voyager pattern."""
        name = action.get("name", "")
        code = action.get("code", "")
        desc = action.get("description", demand.description[:60])

        if not name or not code:
            _logger.info("DemandSolver: create_tool missing name or code")
            return "skip"

        # Sandbox validation
        try:
            from agos.evolution.sandbox import Sandbox
            sandbox = Sandbox(timeout=10)
            result = sandbox.validate(code)
            if not result.safe:
                # Reflexion: record WHY it failed
                issues = "; ".join(result.issues[:3])
                if self._memory:
                    self._memory.record(EvolutionInsight(
                        cycle=self._cycle,
                        what_tried=f"create_tool:{name} for {demand.description[:40]}",
                        module="tool_evolver",
                        outcome="sandbox_failed",
                        reason=f"Sandbox: {issues[:150]}",
                        what_worked="",
                    ))
                _logger.info("DemandSolver: tool '%s' failed sandbox: %s", name, issues[:60])
                return "skip"
        except Exception as e:
            _logger.info("DemandSolver: sandbox error for tool '%s': %s", name, e)
            return "skip"

        # Deploy via ToolEvolver if available
        if tool_evolver:
            try:
                from agos.evolution.tool_evolver import ToolNeed
                need = ToolNeed(name=name, description=desc, source="demand_solver")
                deployed = await tool_evolver.deploy_tool(code, need)
                if deployed:
                    # Success! Record with what_worked (makes it visible to sub-agents)
                    if self._memory:
                        self._memory.record(EvolutionInsight(
                            cycle=self._cycle,
                            what_tried=f"create_tool:{name} for {demand.description[:40]}",
                            module="tool_evolver",
                            outcome="success",
                            reason=f"Deployed tool '{name}': {desc[:80]}",
                            what_worked=f"Created tool '{name}' that {desc[:80]}",
                            recommendation=f"Use tool '{name}' instead of shell workaround",
                            principle=f"Tool '{name}' available for {desc[:60]}",
                            applies_when=demand.kind,
                            scenario_type=demand.source.split("_")[0] if "_" in demand.source else "",
                            env_context=env[:200],
                            confidence=1.0,
                        ))
                    self._demands.clear_resolved(f"{demand.kind}:{demand.source}")
                    _logger.info("DemandSolver: deployed tool '%s'", name)
                    await self._bus.emit("evolution.tool_created", {
                        "name": name, "description": desc[:100],
                    }, source="demand_solver")
                    return "tool_created"
            except Exception as e:
                _logger.info("DemandSolver: deploy failed for '%s': %s", name, e)

        # Fallback: save code to disk for manual loading
        evolved_dir = Path(".opensculpt/evolved")
        evolved_dir.mkdir(parents=True, exist_ok=True)
        (evolved_dir / f"{name}.py").write_text(code, encoding="utf-8")
        if self._memory:
            self._memory.record(EvolutionInsight(
                cycle=self._cycle,
                what_tried=f"create_tool:{name} (saved to disk)",
                module="tool_evolver",
                outcome="success",
                reason=f"Saved tool '{name}' to .opensculpt/evolved/{name}.py",
                what_worked=f"Created tool '{name}' that {desc[:80]}",
                confidence=0.7,
            ))
        _logger.info("DemandSolver: saved tool '%s' to disk", name)
        return "tool_created"

    async def _handle_create_skill_doc(self, demand, action, env) -> str:
        """Write a skill doc that gets injected into future agent prompts."""
        topic = action.get("topic", "general")
        content = action.get("content", "")

        if not content:
            return "skip"

        skills_dir = Path(".opensculpt/skills")
        skills_dir.mkdir(parents=True, exist_ok=True)
        filepath = skills_dir / f"{topic}.md"
        filepath.write_text(content, encoding="utf-8")

        if self._memory:
            self._memory.record(EvolutionInsight(
                cycle=self._cycle,
                what_tried=f"create_skill_doc:{topic} for {demand.description[:40]}",
                module=demand.source,
                outcome="success",
                reason=f"Wrote skill doc: {topic}.md ({len(content)} chars)",
                what_worked=f"Skill doc '{topic}.md' — {content[:80]}",
                recommendation=f"See .opensculpt/skills/{topic}.md for how-to",
                principle=f"Skill doc available: {topic}",
                applies_when=demand.kind,
                confidence=0.9,
            ))
        self._demands.clear_resolved(f"{demand.kind}:{demand.source}")
        _logger.info("DemandSolver: wrote skill doc '%s.md' (%d chars)", topic, len(content))
        await self._bus.emit("evolution.skill_doc_created", {
            "topic": topic, "size": len(content),
        }, source="demand_solver")
        return "principle"  # counts as a principle for reporting

    @staticmethod
    def _classify_failure(demand: DemandSignal) -> str:
        """Classify failure type to guide the LLM's action choice.

        From evolution_patterns.md research:
        - transient: network timeout, rate limit → clear/retry
        - environmental: Docker not installed, port in use → tell_user
        - bug: crash, wrong output → patch_source
        - capability_gap: need browser, need PDF → create_tool
        - architecture: wrong approach → tell_user with context
        """
        desc = demand.description.lower()
        kind = demand.kind

        # Transient
        if any(w in desc for w in ("timeout", "rate limit", "429", "503", "connection reset", "retry")):
            return "transient"

        # Environmental
        if any(w in desc for w in ("not installed", "not found", "not available", "no docker",
                                    "command not found", "permission denied", "port in use")):
            return "environmental"

        # Capability gap
        if kind == "missing_tool" or "missing" in desc or "no tool" in desc:
            return "capability_gap"

        # Agent crash
        if kind == "agent_crash" or "oom" in desc or "killed" in desc:
            return "bug"

        # Code bug (default for errors)
        if kind == "error":
            return "bug"

        return "unknown"

    async def _try_vibe_tool(self, demand: DemandSignal, env: str) -> str:
        """Prepare context for user's vibe coding tool and emit enriched escalation.

        Does NOT invoke any tool. Refreshes DEMANDS.md so context is ready,
        looks up the user's preferred tool, and emits a richer event so the
        dashboard/CLI can show tool-specific instructions.
        """
        # 1. Refresh DEMANDS.md + IDE rule files
        try:
            from agos.evolution.nudge import write_demands_md, write_tool_configs
            write_demands_md()
            write_tool_configs()
        except Exception:
            pass

        # 2. Look up preferred vibe tool
        tool_name = ""
        how_to_use = "Run: sculpt demands --prompt"
        try:
            from agos.setup_store import get_preferred_vibe_tool
            from agos.vibe_tools import get_tool_by_name
            from agos.config import settings
            pref = get_preferred_vibe_tool(Path(settings.workspace_dir))
            if pref:
                tool = get_tool_by_name(pref)
                if tool:
                    tool_name = tool.label
                    how_to_use = tool.how_to_use
        except Exception:
            pass

        # 3. Emit enriched event — dashboard + CLI pick this up
        await self._bus.emit("evolution.user_action_needed", {
            "message": demand.description,
            "demand": demand.description[:60],
            "tool": tool_name,
            "how_to_use": how_to_use,
        }, source="demand_solver")

        _logger.info("DemandSolver: escalating to user (tool=%s) — %s",
                     tool_name or "none", demand.description[:60])
        return "told_user"

    async def _research(self, demand: DemandSignal) -> str:
        """Search web for solutions. Cached per demand key."""
        cache_key = demand.description[:60]
        if cache_key in _research_cache:
            return _research_cache[cache_key]

        # Build search query from demand
        query_parts = []
        if "docker" in demand.description.lower():
            query_parts.append("docker")
        if "not found" in demand.description.lower():
            # Extract the tool/command name
            m = re.search(r"'([^']+)' not found", demand.description)
            if m:
                query_parts.append(m.group(1))
            query_parts.append("alternative python")
        elif "exit=" in demand.description:
            query_parts.append("command failed")
        else:
            # Use first meaningful words from description
            words = demand.description.split()[:5]
            query_parts.extend(w for w in words if len(w) > 3)

        query_parts.append("solution")
        query = " ".join(query_parts[:6])

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code != 200:
                    return ""

                # Extract result snippets (basic regex, no BeautifulSoup)
                snippets = re.findall(
                    r'class="result__snippet"[^>]*>(.*?)</[^>]+>',
                    resp.text, re.DOTALL,
                )
                # Clean HTML tags
                clean = []
                for s in snippets[:3]:
                    text = re.sub(r'<[^>]+>', '', s).strip()
                    if text:
                        clean.append(text[:200])

                result = "\n".join(clean) if clean else ""
                _research_cache[cache_key] = result
                if result:
                    _logger.info("DemandSolver: researched '%s' — found %d snippets", query[:40], len(clean))
                return result
        except Exception as e:
            _logger.debug("DemandSolver: research failed: %s", e)
            return ""
