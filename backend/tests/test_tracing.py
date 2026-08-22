import time

import pytest

from app.agent.tracing import TraceRecorder


def test_step_records_measured_duration() -> None:
    trace = TraceRecorder()
    with trace.step("Slow Step") as step:
        time.sleep(0.02)
        step.summary = "did work"

    (recorded,) = trace.steps
    assert recorded.name == "Slow Step"
    assert recorded.status == "completed"
    assert recorded.summary == "did work"
    # 20ms of real sleep must be reflected; the old implementation reported a literal.
    assert recorded.duration_ms >= 15


def test_steps_are_recorded_in_order() -> None:
    trace = TraceRecorder()
    for name in ("Input Guardrail", "Planner", "Citation Validator"):
        with trace.step(name):
            pass

    assert [step.name for step in trace.steps] == [
        "Input Guardrail",
        "Planner",
        "Citation Validator",
    ]


def test_handle_fail_marks_the_step_without_raising() -> None:
    trace = TraceRecorder()
    with trace.step("Ollama Answer Generator") as step:
        step.fail("model missing")

    (recorded,) = trace.steps
    assert recorded.status == "failed"
    assert recorded.summary == "model missing"


def test_exception_records_a_failed_step_and_propagates() -> None:
    trace = TraceRecorder()
    with pytest.raises(RuntimeError), trace.step("Hybrid Retriever"):
        raise RuntimeError("connection reset")

    (recorded,) = trace.steps
    assert recorded.status == "failed"
    assert "connection reset" in recorded.summary
