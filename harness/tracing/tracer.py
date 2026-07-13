"""P7 — Observability / Tracer.

The Orchestrator runs synchronously (often inside a worker thread, since the
provider call is a blocking HTTP request). The UI consumes events over SSE
on the asyncio event loop. `Tracer` bridges the two: spans are opened/closed
with a plain sync context manager, and each event is handed to the event
loop thread-safely so async subscribers (the SSE stream) see it live.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from contextlib import contextmanager
from typing import AsyncIterator, Iterator

from harness.tracing.events import EventStatus, SpanKind, TraceEvent

_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_span_id", default=None
)

# Sentinel pushed to subscriber queues to signal "the run is over, stop
# iterating" — lets the SSE generator close the HTTP stream cleanly.
_DONE = object()


class Span:
    def __init__(self, span_id: str, parent_span_id: str | None):
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.attrs: dict = {}

    def set_attr(self, key: str, value) -> None:
        self.attrs[key] = value


class Tracer:
    def __init__(self, run_id: str | None = None, loop: asyncio.AbstractEventLoop | None = None):
        self.run_id = run_id or uuid.uuid4().hex
        self._loop = loop
        self._events: list[TraceEvent] = []
        self._subscribers: list[asyncio.Queue] = []
        self._finished = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the asyncio loop that SSE subscribers live on."""
        self._loop = loop

    @contextmanager
    def span(self, name: str, kind: SpanKind, attrs: dict | None = None) -> Iterator[Span]:
        span_id = uuid.uuid4().hex
        parent_id = _current_span_id.get()
        span = Span(span_id, parent_id)
        span.attrs = dict(attrs or {})

        self._emit(TraceEvent(
            run_id=self.run_id, span_id=span_id, parent_span_id=parent_id,
            name=name, kind=kind, status="started", attrs=dict(span.attrs),
        ))

        token = _current_span_id.set(span_id)
        start = time.monotonic()
        status: EventStatus = "ok"
        try:
            yield span
        except Exception as exc:
            status = "error"
            span.set_attr("error", str(exc))
            raise
        finally:
            _current_span_id.reset(token)
            duration_ms = (time.monotonic() - start) * 1000
            self._emit(TraceEvent(
                run_id=self.run_id, span_id=span_id, parent_span_id=parent_id,
                name=name, kind=kind, status=status, attrs=dict(span.attrs),
                duration_ms=duration_ms,
            ))

    def event(self, name: str, kind: SpanKind, attrs: dict | None = None) -> None:
        """Emit a single instantaneous event (no duration) — e.g. an
        approval-gate decision or a skill load."""
        self._emit(TraceEvent(
            run_id=self.run_id, span_id=uuid.uuid4().hex,
            parent_span_id=_current_span_id.get(), name=name, kind=kind,
            status="ok", attrs=dict(attrs or {}),
        ))

    def finish(self) -> None:
        self._finished = True
        self._broadcast(_DONE)

    def _emit(self, event: TraceEvent) -> None:
        self._events.append(event)
        self._broadcast(event)

    def _broadcast(self, item) -> None:
        if self._loop is None:
            return
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, item)

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    async def subscribe(self) -> AsyncIterator[TraceEvent]:
        q: asyncio.Queue = asyncio.Queue()
        for e in self._events:
            q.put_nowait(e)
        if self._finished:
            q.put_nowait(_DONE)
        self._subscribers.append(q)
        try:
            while True:
                item = await q.get()
                if item is _DONE:
                    return
                yield item
        finally:
            self._subscribers.remove(q)
