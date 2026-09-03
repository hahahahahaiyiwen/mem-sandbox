"""Required and owner-managed best-effort event dispatch."""

from __future__ import annotations

import asyncio
import inspect
from typing import Protocol, cast

from mem_sandbox.core import SandboxError
from mem_sandbox.events.errors import (
    EventBufferLimitExceeded,
    EventDeliveryFailed,
    EventSinkClosed,
)
from mem_sandbox.events.models import (
    EventDeliveryDiagnostic,
    EventDeliveryMode,
    EventDeliveryPolicy,
    EventPayloadLimits,
    EventPayloadPolicy,
    SandboxEvent,
)
from mem_sandbox.events.redaction import ProtectedValueRedactor, prepare_event

_STOP = object()


class EventSink(Protocol):
    """Accept one already-prepared event."""

    async def emit(self, event: SandboxEvent) -> None: ...


class OwnedEventSink(EventSink, Protocol):
    """Expose sink lifecycle only to its constructing resource owner."""

    async def flush(self) -> None: ...

    async def close(self) -> None: ...


class EventDiagnosticHandler(Protocol):
    """Synchronously report one best-effort failure."""

    def report(self, diagnostic: EventDeliveryDiagnostic) -> None: ...


class EventDispatcher:
    """Prepare events and apply one immutable delivery mode."""

    def __init__(
        self,
        sink: EventSink,
        *,
        delivery_policy: EventDeliveryPolicy | None = None,
        payload_policy: EventPayloadPolicy | None = None,
        payload_limits: EventPayloadLimits | None = None,
        redactor: ProtectedValueRedactor | None = None,
        diagnostic_handler: EventDiagnosticHandler | None = None,
    ) -> None:
        delivery_policy = delivery_policy or EventDeliveryPolicy()
        payload_policy = payload_policy or EventPayloadPolicy()
        payload_limits = payload_limits or EventPayloadLimits()
        if delivery_policy.mode is EventDeliveryMode.BEST_EFFORT and diagnostic_handler is None:
            raise ValueError("best-effort delivery requires a diagnostic handler")
        if diagnostic_handler is not None and inspect.iscoroutinefunction(
            diagnostic_handler.report
        ):
            raise TypeError("event diagnostic handler must be synchronous")
        self._sink = sink
        self._delivery_policy = delivery_policy
        self._payload_policy = payload_policy
        self._payload_limits = payload_limits
        self._redactor = ProtectedValueRedactor() if redactor is None else redactor
        self._diagnostic_handler = diagnostic_handler
        self._queue: asyncio.Queue[SandboxEvent | object] | None = (
            asyncio.Queue(maxsize=delivery_policy.max_pending_events)
            if delivery_policy.mode is EventDeliveryMode.BEST_EFFORT
            else None
        )
        self._worker: asyncio.Task[None] | None = None
        self._accepting = True
        self._close_task: asyncio.Task[None] | None = None

    @property
    def delivery_policy(self) -> EventDeliveryPolicy:
        return self._delivery_policy

    async def emit(self, event: SandboxEvent) -> None:
        if self._delivery_policy.mode is EventDeliveryMode.REQUIRED:
            if not self._accepting:
                raise EventSinkClosed("event dispatcher is closed")
            prepared = self._prepare(event)
            try:
                await self._sink.emit(prepared)
            except asyncio.CancelledError:
                raise
            except SandboxError:
                raise
            except Exception as error:
                raise EventDeliveryFailed("event delivery failed") from error
            return

        if not self._accepting:
            self._report(
                event,
                EventSinkClosed("event dispatcher is closed"),
            )
            return
        try:
            prepared = self._prepare(event)
        except Exception as error:
            self._report(event, error)
            return

        queue = self._required_queue()
        self._ensure_worker(queue)
        try:
            queue.put_nowait(prepared)
        except asyncio.QueueFull:
            self._report(
                event,
                EventBufferLimitExceeded("best-effort event queue is full"),
            )

    async def flush(self) -> None:
        queue = self._queue
        if queue is not None:
            await queue.join()

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is None:
            self._accepting = False
            close_task = asyncio.create_task(self._close_once())
            self._close_task = close_task
        if close_task.done():
            await close_task
            return
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(close_task)
            finally:
                raise

    async def _close_once(self) -> None:
        queue = self._queue
        worker = self._worker
        if queue is None or worker is None:
            return
        await queue.join()
        if worker.done():
            await worker
            return
        await queue.put(_STOP)
        await worker

    def _prepare(self, event: SandboxEvent) -> SandboxEvent:
        return prepare_event(
            event,
            redactor=self._redactor,
            payload_policy=self._payload_policy,
            limits=self._payload_limits,
        )

    def _required_queue(self) -> asyncio.Queue[SandboxEvent | object]:
        if self._queue is None:
            raise RuntimeError("best-effort queue is unavailable")
        return self._queue

    def _ensure_worker(self, queue: asyncio.Queue[SandboxEvent | object]) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker(queue))

    async def _run_worker(
        self,
        queue: asyncio.Queue[SandboxEvent | object],
    ) -> None:
        try:
            while True:
                queued = await queue.get()
                try:
                    if queued is _STOP:
                        return
                    if not isinstance(queued, SandboxEvent):
                        asyncio.get_running_loop().call_exception_handler(
                            {
                                "message": "event dispatcher received an invalid queue item",
                                "queue_item": queued,
                            }
                        )
                        continue
                    event = queued
                    try:
                        async with asyncio.timeout(self._delivery_policy.sink_timeout_seconds):
                            await self._sink.emit(event)
                    except asyncio.CancelledError as error:
                        current = asyncio.current_task()
                        if current is not None and current.cancelling():
                            raise
                        self._report(event, error)
                    except Exception as error:
                        self._report(event, error)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as worker_error:
            asyncio.get_running_loop().call_exception_handler(
                {
                    "message": "event dispatcher worker failed",
                    "exception": worker_error,
                }
            )
            self._fail_queued_events(queue, worker_error)

    def _fail_queued_events(
        self,
        queue: asyncio.Queue[SandboxEvent | object],
        worker_error: Exception,
    ) -> None:
        while True:
            try:
                queued = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if isinstance(queued, SandboxEvent):
                    self._report(queued, worker_error)
            finally:
                queue.task_done()

    def _report(self, event: SandboxEvent, error: BaseException) -> None:
        handler = self._diagnostic_handler
        if handler is None:
            asyncio.get_running_loop().call_exception_handler(
                {
                    "message": "event diagnostic handler is unavailable",
                    "exception": error,
                    "event": event,
                }
            )
            return
        code_value = getattr(type(error), "code", None)
        code = (
            code_value if isinstance(code_value, str) and code_value else EventDeliveryFailed.code
        )
        message = (
            str(error)
            if isinstance(error, SandboxError) and str(error)
            else f"event delivery failed ({type(error).__name__})"
        )
        diagnostic = EventDeliveryDiagnostic(
            event_id=event.event_id,
            failure_code=code,
            message=message,
        )
        try:
            result = cast(object, handler.report(diagnostic))
            if result is not None:
                if inspect.iscoroutine(result):
                    result.close()
                elif isinstance(result, asyncio.Future):
                    result.cancel()
                raise TypeError("event diagnostic handler must return None")
        except Exception as handler_error:
            asyncio.get_running_loop().call_exception_handler(
                {
                    "message": "event diagnostic handler failed",
                    "exception": handler_error,
                    "event_diagnostic": diagnostic,
                }
            )
