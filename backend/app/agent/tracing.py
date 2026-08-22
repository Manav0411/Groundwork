"""Measured agent trace steps.

Trace durations were previously hardcoded literals that were persisted to PostgreSQL and
rendered in the dashboard as if they were measurements. Every step now records the real
elapsed time around the work it names.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from app.models.schemas import TraceStep


class StepHandle:
    """Mutable handle for the step currently being timed."""

    def __init__(self, name: str, summary: str) -> None:
        self.name = name
        self.summary = summary
        self.status: str = "completed"

    def fail(self, summary: str) -> None:
        self.status = "failed"
        self.summary = summary


class TraceRecorder:
    """Collects `TraceStep` entries with durations measured by `perf_counter`."""

    def __init__(self) -> None:
        self._steps: list[TraceStep] = []

    @contextmanager
    def step(self, name: str, summary: str = "") -> Iterator[StepHandle]:
        """Time the body and append one trace step.

        The handle's `summary` may be reassigned inside the block, so a node reports what it
        actually did. An exception inside the block is recorded as a failed step and re-raised.
        """
        handle = StepHandle(name, summary)
        started = perf_counter()
        try:
            yield handle
        except Exception as exc:
            if handle.status != "failed":
                handle.fail(f"{handle.summary or name} failed: {exc}".strip())
            self._append(handle, started)
            raise
        self._append(handle, started)

    def _append(self, handle: StepHandle, started: float) -> None:
        # Sub-millisecond steps honestly record 0; the deterministic SQL path really is that fast.
        duration_ms = round((perf_counter() - started) * 1_000)
        self._steps.append(
            TraceStep(
                name=handle.name,
                status=handle.status,  # type: ignore[arg-type]
                duration_ms=duration_ms,
                summary=handle.summary,
            )
        )

    @property
    def steps(self) -> list[TraceStep]:
        return list(self._steps)
