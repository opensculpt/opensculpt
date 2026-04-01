"""Distributed Tracing — tracks causal chains across agent interactions.

When agent A spawns agent B which calls tool C, the trace captures
the full chain so you can debug and visualize the flow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agos.types import new_id


class Span(BaseModel):
    """A single span in a trace — one unit of work."""

    id: str = Field(default_factory=new_id)
    trace_id: str = ""
    parent_id: str = ""
    name: str = ""
    kind: str = "internal"  # "agent", "tool", "llm", "internal"
    agent_id: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = "ok"  # "ok", "error"
    error: str = ""


class Trace(BaseModel):
    """A full execution trace — a tree of spans."""

    id: str = Field(default_factory=new_id)
    root_span_id: str = ""
    spans: list[Span] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def duration_ms(self) -> float:
        if not self.spans:
            return 0.0
        started = min(s.started_at for s in self.spans)
        ended = max(s.ended_at or s.started_at for s in self.spans)
        return (ended - started).total_seconds() * 1000

    @property
    def span_count(self) -> int:
        return len(self.spans)

    @property
    def error_count(self) -> int:
        return sum(1 for s in self.spans if s.status == "error")


class Tracer:
    """Creates and manages execution traces."""

    def __init__(self) -> None:
        self._traces: dict[str, Trace] = {}
        self._active_spans: dict[str, Span] = {}

    def start_trace(self, name: str = "") -> Trace:
        """Begin a new trace."""
        trace = Trace()
        self._traces[trace.id] = trace
        return trace

    def start_span(
        self,
        trace_id: str,
        name: str,
        kind: str = "internal",
        parent_id: str = "",
        agent_id: str = "",
        metadata: dict | None = None,
    ) -> Span:
        """Start a new span within a trace."""
        span = Span(
            trace_id=trace_id,
            parent_id=parent_id,
            name=name,
            kind=kind,
            agent_id=agent_id,
            metadata=metadata or {},
        )

        trace = self._traces.get(trace_id)
        if trace:
            trace.spans.append(span)
            if not trace.root_span_id:
                trace.root_span_id = span.id

        self._active_spans[span.id] = span
        return span

    def end_span(self, span_id: str, status: str = "ok", error: str = "") -> Span | None:
        """End a span and record its duration."""
        span = self._active_spans.pop(span_id, None)
        if span is None:
            return None

        span.ended_at = datetime.utcnow()
        span.duration_ms = (span.ended_at - span.started_at).total_seconds() * 1000
        span.status = status
        span.error = error
        return span

    def get_trace(self, trace_id: str) -> Trace | None:
        return self._traces.get(trace_id)

    def list_traces(self, limit: int = 20) -> list[Trace]:
        """Get recent traces, newest first."""
        traces = sorted(
            self._traces.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        return traces[:limit]

    @property
    def active_span_count(self) -> int:
        return len(self._active_spans)
