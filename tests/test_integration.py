"""End-to-end integration tests — verifies all subsystems work together.

These tests use MockLLMProvider (no API calls) to validate the full
pipeline: Intent Engine → Planner → Agent Runtime → Tools → Knowledge.
"""

import asyncio
import tempfile

import pytest
import pytest_asyncio

from agos.types import AgentDefinition, AgentState
from agos.llm.base import LLMResponse, ToolCall
from agos.kernel.runtime import AgentRuntime
from agos.tools.registry import ToolRegistry
from agos.tools.builtins import register_builtin_tools
from agos.intent.engine import IntentEngine
from agos.intent.planner import Planner
from agos.knowledge.manager import TheLoom
from agos.knowledge.base import ThreadQuery
from agos.triggers.base import TriggerConfig
from agos.triggers.manager import TriggerManager

from tests.conftest import MockLLMProvider


@pytest.fixture
def tools():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


@pytest_asyncio.fixture
async def loom():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    the_loom = TheLoom(db_path)
    await the_loom.initialize()
    return the_loom


# ── Sprint 1: Intent → Agent → Tool → Result ────────────────────────


@pytest.mark.asyncio
async def test_agent_executes_tool_and_returns_result(tools):
    """An agent receives a tool call from the LLM, executes it, and finishes."""
    mock = MockLLMProvider([
        # Turn 1: LLM asks to use file_read
        LLMResponse(
            content="Let me read the file.",
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="t1", name="file_read", arguments={"path": __file__})],
            input_tokens=50,
            output_tokens=20,
        ),
        # Turn 2: After tool result, LLM gives final answer
        LLMResponse(
            content="The file contains integration tests for agos.",
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=30,
        ),
    ])

    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)
    defn = AgentDefinition(
        name="test-analyst",
        system_prompt="You analyze files.",
        tools=["file_read"],
    )

    agent = await runtime.spawn(defn, user_message="What's in this file?")
    result = await agent.wait()

    assert result == "The file contains integration tests for agos."
    assert agent.state == AgentState.COMPLETED
    assert agent.context.turns == 2
    assert agent.context.tokens_used > 0

    # LLM was called twice (initial + after tool result)
    assert len(mock.calls) == 2


@pytest.mark.asyncio
async def test_intent_engine_produces_valid_plan():
    """IntentEngine parses LLM response into a valid ExecutionPlan."""
    mock = MockLLMProvider([
        LLMResponse(
            content="""{
                "intent_type": "research",
                "description": "Analyze the codebase structure",
                "agents": ["analyst"],
                "strategy": "solo"
            }""",
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=100,
        ),
    ])

    engine = IntentEngine(mock)
    plan = await engine.understand("analyze my codebase")

    assert plan.intent_type.value == "research"
    assert plan.strategy.value == "solo"
    assert len(plan.agents) >= 1
    assert plan.agents[0].name == "analyst"


@pytest.mark.asyncio
async def test_full_pipeline_intent_to_result(tools):
    """Full pipeline: understand intent → create plan → execute agents → get result."""
    # Mock for intent engine (understanding phase)
    intent_mock = MockLLMProvider([
        LLMResponse(
            content="""{
                "intent_type": "answer",
                "description": "Answer user question",
                "agents": ["analyst"],
                "strategy": "solo"
            }""",
            stop_reason="end_turn",
            input_tokens=30,
            output_tokens=80,
        ),
    ])

    # Mock for agent execution (runtime phase)
    agent_mock = MockLLMProvider([
        LLMResponse(
            content="agos has 5 built-in tools for file, shell, HTTP, and Python operations.",
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=15,
        ),
    ])

    # Build the pipeline
    engine = IntentEngine(intent_mock)
    runtime = AgentRuntime(llm_provider=agent_mock, tool_registry=tools)
    planner = Planner(runtime)

    # Execute
    plan = await engine.understand("what tools does agos have?")
    result = await planner.execute(plan, "what tools does agos have?")

    assert "tools" in result.lower() or "operations" in result.lower()


# ── Sprint 2: Knowledge System ──────────────────────────────────────


@pytest.mark.asyncio
async def test_learner_records_full_interaction(loom):
    """Learner records interaction across episodic, semantic, and graph weaves."""
    await loom.learner.record_interaction(
        agent_id="agent-001",
        agent_name="analyst",
        user_input="How does Python GIL work?",
        agent_output="The GIL is a mutex that protects access to Python objects.",
        tokens_used=150,
    )

    # Episodic: event was recorded
    events = await loom.episodic.query(ThreadQuery(agent_id="agent-001"))
    assert len(events) >= 1

    # Semantic: can recall by content
    results = await loom.recall("Python GIL")
    assert len(results) >= 1

    # Graph: agent linked to interaction (stored by agent_name, not agent_id)
    conns = await loom.graph.connections("agent:analyst")
    assert len(conns) >= 1


@pytest.mark.asyncio
async def test_knowledge_accumulates_across_interactions(loom):
    """Multiple interactions build up knowledge that can be recalled."""
    await loom.learner.record_interaction(
        agent_id="a1", agent_name="coder",
        user_input="Write a sorting algorithm",
        agent_output="Here's quicksort in Python",
        tokens_used=100,
    )
    await loom.learner.record_interaction(
        agent_id="a2", agent_name="researcher",
        user_input="What is the fastest sorting algorithm?",
        agent_output="Timsort is used by Python internally",
        tokens_used=80,
    )

    results = await loom.recall("sorting algorithm")
    assert len(results) >= 2

    timeline = await loom.timeline(limit=10)
    assert len(timeline) >= 2


@pytest.mark.asyncio
async def test_remember_and_recall_semantic(loom):
    """Store explicit knowledge and retrieve it via semantic search."""
    await loom.remember("agos uses Claude as its LLM backbone", kind="fact")
    await loom.remember("Triggers enable ambient intelligence", kind="fact")
    await loom.remember("The Loom is the knowledge substrate", kind="fact")

    results = await loom.recall("what LLM does agos use")
    assert len(results) >= 1
    assert any("claude" in r.content.lower() for r in results)


# ── Sprint 3: Triggers ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_fires_and_calls_handler():
    """Schedule trigger fires on interval and handler receives intent."""
    manager = TriggerManager()
    received = []

    async def handler(intent: str):
        received.append(intent)

    manager.set_handler(handler)

    config = TriggerConfig(
        kind="schedule",
        description="Quick test",
        intent="check status",
        params={"interval_seconds": 0.1, "max_fires": 2},
    )

    await manager.register(config)
    await asyncio.sleep(0.5)
    await manager.stop_all()

    assert len(received) == 2
    assert all("check status" in r for r in received)


@pytest.mark.asyncio
async def test_file_watcher_detects_changes():
    """File watcher trigger detects new files in a directory."""
    import tempfile
    from pathlib import Path

    manager = TriggerManager()
    received = []

    async def handler(intent: str):
        received.append(intent)

    manager.set_handler(handler)

    with tempfile.TemporaryDirectory() as tmpdir:
        config = TriggerConfig(
            kind="file_watch",
            description="Watch temp dir",
            intent="review changes",
            params={"path": tmpdir, "interval": 0.2, "patterns": ["*"]},
        )
        await manager.register(config)

        # Wait for initial snapshot
        await asyncio.sleep(0.3)

        # Create a file
        Path(tmpdir, "new.py").write_text("print('hello')")

        # Wait for detection
        await asyncio.sleep(0.5)
        await manager.stop_all()

    assert len(received) >= 1
    assert "review changes" in received[0]


@pytest.mark.asyncio
async def test_webhook_trigger_routes_payload():
    """Webhook trigger receives payload and routes through handler."""
    manager = TriggerManager()
    received = []

    async def handler(intent: str):
        received.append(intent)

    manager.set_handler(handler)

    config = TriggerConfig(
        kind="webhook",
        description="GitHub events",
        intent="process webhook",
        params={"path": "/hooks/github"},
    )
    trigger = await manager.register(config)

    # Simulate incoming webhook
    from agos.triggers.webhook import WebhookTrigger
    assert isinstance(trigger, WebhookTrigger)
    await trigger.receive({"action": "push", "ref": "main"})

    await asyncio.sleep(0.3)
    await manager.stop_all()

    assert len(received) >= 1
    assert "process webhook" in received[0]


# ── Cross-Sprint: Full System Integration ────────────────────────────


@pytest.mark.asyncio
async def test_agent_with_knowledge_persistence(tools, loom):
    """Agent runs, result is stored in knowledge, can be recalled later."""
    mock = MockLLMProvider([
        LLMResponse(
            content="Your codebase has 31 modules across 6 packages.",
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=10,
        ),
    ])

    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)
    defn = AgentDefinition(name="analyst", system_prompt="Analyze code.", tools=[])

    agent = await runtime.spawn(defn, user_message="How big is my codebase?")
    result = await agent.wait()

    # Store the interaction in knowledge
    await loom.learner.record_interaction(
        agent_id=agent.id,
        agent_name="analyst",
        user_input="How big is my codebase?",
        agent_output=result,
        tokens_used=agent.context.tokens_used,
    )

    # Later: can recall this
    results = await loom.recall("codebase size")
    assert len(results) >= 1
    assert any("31 modules" in r.content for r in results)


@pytest.mark.asyncio
async def test_multiple_agents_parallel(tools):
    """Multiple agents can run in parallel and all complete."""
    mock = MockLLMProvider([
        LLMResponse(content="Agent 1 done.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="Agent 2 done.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
        LLMResponse(content="Agent 3 done.", stop_reason="end_turn", input_tokens=10, output_tokens=5),
    ])

    runtime = AgentRuntime(llm_provider=mock, tool_registry=tools)

    agents = []
    for i in range(3):
        defn = AgentDefinition(name=f"worker-{i}", system_prompt="Work.", tools=[])
        agent = await runtime.spawn(defn, user_message=f"Task {i}")
        agents.append(agent)

    # Wait for all to complete
    results = await asyncio.gather(*[a.wait() for a in agents])

    assert len(results) == 3
    all_agents = runtime.list_agents()
    assert all(a["state"] == "completed" for a in all_agents)
