"""The Agent — the core reasoning entity of agos."""

from __future__ import annotations

import asyncio
from datetime import datetime

from agos.types import AgentId, AgentState, AgentDefinition, new_id
from agos.kernel.state_machine import AgentStateMachine
from agos.llm.base import BaseLLMProvider, LLMMessage, LLMResponse
from agos.exceptions import TokenBudgetExceededError


class AgentContext:
    """Mutable runtime context — tracks what the agent has done."""

    def __init__(self, agent_id: AgentId, definition: AgentDefinition):
        self.agent_id = agent_id
        self.definition = definition
        self.messages: list[LLMMessage] = []
        self.tokens_used: int = 0
        self.turns: int = 0
        self.created_at: datetime = datetime.utcnow()
        self.last_response: LLMResponse | None = None
        self.final_output: str | None = None


class Agent:
    """A reasoning entity managed by the kernel.

    Each agent has a state machine, a conversation context, and runs
    an async loop that calls the LLM and executes tool calls.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        llm: BaseLLMProvider,
        tool_executor: object | None = None,
        agent_id: AgentId | None = None,
    ):
        self.id: AgentId = agent_id or new_id()
        self.definition = definition
        self.llm = llm
        self.tool_executor = tool_executor  # ToolRegistry, injected by runtime
        self.state_machine = AgentStateMachine(self.id)
        self.context = AgentContext(self.id, definition)
        self._task: asyncio.Task | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._cancel_requested = False

    @property
    def state(self) -> AgentState:
        return self.state_machine.state

    async def initialize(self) -> None:
        await self.state_machine.transition(AgentState.READY)

    async def start(self, user_message: str | None = None) -> None:
        """Start the agent's run loop as a background task."""
        if user_message:
            self.context.messages.append(LLMMessage(role="user", content=user_message))
        self._task = asyncio.create_task(self._run_loop(), name=f"agent-{self.id[:8]}")

    async def _run_loop(self) -> None:
        await self.state_machine.transition(AgentState.RUNNING)
        try:
            while not self._cancel_requested:
                await self._pause_event.wait()

                # Budget check
                if self.context.tokens_used >= self.definition.token_budget:
                    raise TokenBudgetExceededError(
                        f"Agent {self.id} exceeded budget: "
                        f"{self.context.tokens_used}/{self.definition.token_budget}"
                    )
                if self.context.turns >= self.definition.max_turns:
                    break

                # Build tools list
                tools = None
                if self.tool_executor and hasattr(self.tool_executor, "get_anthropic_tools"):
                    tools = self.tool_executor.get_anthropic_tools()

                # Call LLM
                response = await self.llm.complete(
                    messages=self.context.messages,
                    system=self.definition.system_prompt,
                    tools=tools,
                )

                self.context.tokens_used += response.input_tokens + response.output_tokens
                self.context.turns += 1
                self.context.last_response = response

                # Append assistant response to conversation
                if response.content and not response.tool_calls:
                    self.context.messages.append(
                        LLMMessage(role="assistant", content=response.content)
                    )

                # Handle tool calls
                if response.tool_calls and self.tool_executor:
                    # Build assistant message with tool_use blocks
                    assistant_content = []
                    if response.content:
                        assistant_content.append({"type": "text", "text": response.content})
                    for tc in response.tool_calls:
                        assistant_content.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                    self.context.messages.append(
                        LLMMessage(role="assistant", content=assistant_content)
                    )

                    # Execute each tool and collect results
                    tool_results_content = []
                    for tc in response.tool_calls:
                        result = await self.tool_executor.execute(tc.name, tc.arguments)
                        tool_results_content.append({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": str(result.result) if result.success else str(result.error),
                            "is_error": not result.success,
                        })

                    self.context.messages.append(
                        LLMMessage(role="user", content=tool_results_content)
                    )
                    continue  # Loop back to get LLM's response to tool results

                # No tool calls and end_turn — agent is done
                if response.stop_reason == "end_turn":
                    self.context.final_output = response.content
                    break

        except asyncio.CancelledError:
            pass
        except TokenBudgetExceededError:
            await self.state_machine.transition(AgentState.ERROR)
            raise
        except Exception:
            await self.state_machine.transition(AgentState.ERROR)
            raise
        else:
            await self.state_machine.transition(AgentState.COMPLETED)

    async def pause(self) -> None:
        self._pause_event.clear()
        await self.state_machine.transition(AgentState.PAUSED)

    async def resume(self) -> None:
        await self.state_machine.transition(AgentState.READY)
        self._pause_event.set()
        await self.state_machine.transition(AgentState.RUNNING)

    async def kill(self) -> None:
        self._cancel_requested = True
        self._pause_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self.state not in (AgentState.COMPLETED, AgentState.TERMINATED, AgentState.ERROR):
            await self.state_machine.transition(AgentState.TERMINATED)

    async def wait(self) -> str | None:
        """Wait for the agent to finish and return its final output."""
        if self._task:
            await self._task
        return self.context.final_output
