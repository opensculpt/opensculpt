"""Tests for the distributed tracing system."""

import time

from agos.events.tracing import Tracer


def test_start_trace():
    tracer = Tracer()
    trace = tracer.start_trace("test-flow")

    assert trace.id
    assert trace.span_count == 0


def test_start_and_end_span():
    tracer = Tracer()
    trace = tracer.start_trace("test")
    span = tracer.start_span(trace.id, "agent.run", kind="agent", agent_id="a1")

    assert span.trace_id == trace.id
    assert span.kind == "agent"
    assert tracer.active_span_count == 1

    ended = tracer.end_span(span.id)
    assert ended is not None
    assert ended.duration_ms >= 0
    assert ended.status == "ok"
    assert tracer.active_span_count == 0


def test_span_error():
    tracer = Tracer()
    trace = tracer.start_trace("test")
    span = tracer.start_span(trace.id, "tool.call")

    ended = tracer.end_span(span.id, status="error", error="Tool not found")
    assert ended.status == "error"
    assert ended.error == "Tool not found"


def test_trace_records_spans():
    tracer = Tracer()
    trace = tracer.start_trace("pipeline")

    s1 = tracer.start_span(trace.id, "step-1")
    tracer.end_span(s1.id)

    s2 = tracer.start_span(trace.id, "step-2", parent_id=s1.id)
    tracer.end_span(s2.id)

    retrieved = tracer.get_trace(trace.id)
    assert retrieved.span_count == 2
    assert retrieved.root_span_id == s1.id


def test_trace_duration():
    tracer = Tracer()
    trace = tracer.start_trace("timing")

    s = tracer.start_span(trace.id, "work")
    time.sleep(0.01)
    tracer.end_span(s.id)

    assert trace.duration_ms > 0


def test_trace_error_count():
    tracer = Tracer()
    trace = tracer.start_trace("mixed")

    s1 = tracer.start_span(trace.id, "ok-step")
    tracer.end_span(s1.id, status="ok")

    s2 = tracer.start_span(trace.id, "bad-step")
    tracer.end_span(s2.id, status="error", error="boom")

    assert trace.error_count == 1


def test_list_traces():
    tracer = Tracer()
    tracer.start_trace("t1")
    tracer.start_trace("t2")
    tracer.start_trace("t3")

    traces = tracer.list_traces()
    assert len(traces) == 3


def test_list_traces_limit():
    tracer = Tracer()
    for i in range(10):
        tracer.start_trace(f"t{i}")

    traces = tracer.list_traces(limit=3)
    assert len(traces) == 3


def test_get_nonexistent_trace():
    tracer = Tracer()
    assert tracer.get_trace("nope") is None


def test_end_nonexistent_span():
    tracer = Tracer()
    assert tracer.end_span("nope") is None


def test_nested_spans():
    tracer = Tracer()
    trace = tracer.start_trace("nested")

    root = tracer.start_span(trace.id, "root", kind="agent")
    child = tracer.start_span(trace.id, "tool-call", kind="tool", parent_id=root.id)
    grandchild = tracer.start_span(trace.id, "llm-call", kind="llm", parent_id=child.id)

    assert grandchild.parent_id == child.id
    assert child.parent_id == root.id

    tracer.end_span(grandchild.id)
    tracer.end_span(child.id)
    tracer.end_span(root.id)

    assert trace.span_count == 3
    assert tracer.active_span_count == 0


def test_span_metadata():
    tracer = Tracer()
    trace = tracer.start_trace("meta")
    span = tracer.start_span(
        trace.id, "tool",
        metadata={"tool_name": "file_read", "path": "/tmp/x"},
    )

    assert span.metadata["tool_name"] == "file_read"
