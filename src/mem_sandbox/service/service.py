"""Race-safe process-local sandbox lifecycle service."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from mem_sandbox.core import Clock, SandboxError, SessionId, UuidGenerator
from mem_sandbox.service.errors import (
    SandboxDeleteFailed,
    SandboxIdentifierConflict,
    SandboxNotFound,
    SandboxResumeFailed,
    SandboxServiceClosed,
    SandboxStartupFailed,
    SessionFactoryFailed,
)
from mem_sandbox.service.models import (
    CreateSandboxRequest,
    OwnerId,
    ResumeSandboxRequest,
    SandboxHandle,
    SessionFactoryRequest,
)
from mem_sandbox.service.ports import (
    ServiceSessionRuntime,
    ServiceSnapshotDecoder,
    ServiceSnapshotGateway,
    SessionFactory,
)
from mem_sandbox.session import SandboxSession, SandboxSessionState


class _ServiceState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class _RecordStatus(StrEnum):
    ACTIVE = "active"
    DELETING = "deleting"


@dataclass(slots=True)
class _SessionRecord:
    handle: SandboxHandle
    owner_id: OwnerId
    session_id: SessionId
    runtime: ServiceSessionRuntime
    created_at: datetime
    status: _RecordStatus = _RecordStatus.ACTIVE
    deletion_task: asyncio.Task[None] | None = None


class InMemorySandboxService:
    """Publish only fully started sessions and own their cleanup."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        snapshot_gateway: ServiceSnapshotGateway,
        snapshot_decoder: ServiceSnapshotDecoder,
        clock: Clock,
        uuid_generator: UuidGenerator,
    ) -> None:
        self._session_factory = session_factory
        self._snapshot_gateway = snapshot_gateway
        self._snapshot_decoder = snapshot_decoder
        self._clock = clock
        self._uuid_generator = uuid_generator
        self._lock = asyncio.Lock()
        self._state = _ServiceState.OPEN
        self._records: dict[SandboxHandle, _SessionRecord] = {}
        self._reserved_handles: set[SandboxHandle] = set()
        self._reserved_session_ids: set[SessionId] = set()
        self._pending_operations: set[asyncio.Future[None]] = set()
        self._close_task: asyncio.Task[None] | None = None

    async def create(self, request: CreateSandboxRequest) -> SandboxHandle:
        operation = await self._begin_lifecycle()
        handle: SandboxHandle | None = None
        session_id: SessionId | None = None
        runtime: ServiceSessionRuntime | None = None
        try:
            handle, session_id = await self._reserve_identifiers()
            try:
                snapshot_store = self._snapshot_gateway.session_store(request.owner_id)
                runtime = await self._session_factory.create(
                    SessionFactoryRequest(
                        session_id=session_id,
                        options=request.options,
                        snapshot_store=snapshot_store,
                        initial_files=request.initial_files,
                    )
                )
            except asyncio.CancelledError:
                raise
            except SandboxError:
                raise
            except BaseException as error:
                raise SessionFactoryFailed("session factory construction failed") from error
            try:
                await runtime.session.start()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                failure = SandboxStartupFailed("sandbox startup failed")
                await _cleanup_after_failure(runtime, failure)
                runtime = None
                raise failure from error
            await self._publish(
                handle,
                request.owner_id,
                session_id,
                runtime,
            )
            runtime = None
            return handle
        finally:
            primary = sys.exception()
            try:
                if runtime is not None:
                    await _cleanup_after_failure(runtime, primary)
            finally:
                await self._finish_lifecycle(operation, handle, session_id)

    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle:
        operation = await self._begin_lifecycle()
        handle: SandboxHandle | None = None
        session_id: SessionId | None = None
        runtime: ServiceSessionRuntime | None = None
        try:
            snapshot = await self._snapshot_gateway.load(request.snapshot_ref)
            restored_state = self._snapshot_decoder.decode(snapshot)
            handle, session_id = await self._reserve_identifiers()
            try:
                runtime = await self._session_factory.create(
                    SessionFactoryRequest(
                        session_id=session_id,
                        options=request.options,
                        snapshot_store=self._snapshot_gateway.session_store(request.owner_id),
                        restored_state=restored_state,
                    )
                )
            except asyncio.CancelledError:
                raise
            except SessionFactoryFailed as error:
                raise SandboxResumeFailed("sandbox resume failed") from error
            except SandboxError:
                raise
            except BaseException as error:
                raise SandboxResumeFailed("sandbox resume failed") from error
            try:
                await runtime.session.start()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                raise SandboxResumeFailed("sandbox resume failed") from error
            await self._publish(
                handle,
                request.owner_id,
                session_id,
                runtime,
            )
            runtime = None
            return handle
        except asyncio.CancelledError:
            raise
        except SandboxError:
            raise
        except BaseException as error:
            raise SandboxResumeFailed("sandbox resume failed") from error
        finally:
            primary = sys.exception()
            try:
                if runtime is not None:
                    await _cleanup_after_failure(runtime, primary)
            finally:
                await self._finish_lifecycle(operation, handle, session_id)

    async def get_session(self, handle: SandboxHandle) -> SandboxSession:
        deletion_task: asyncio.Task[None] | None = None
        async with self._lock:
            self._require_open()
            record = self._records.get(handle)
            if record is None or record.status is _RecordStatus.DELETING:
                raise SandboxNotFound(f"sandbox {handle} does not exist")
            if record.runtime.session.state is SandboxSessionState.RUNNING:
                return record.runtime.session
            deletion_task = self._start_deletion_locked(record)
        try:
            await _await_shared(deletion_task)
        except SandboxDeleteFailed:
            raise
        raise SandboxNotFound(f"sandbox {handle} does not exist")

    async def delete(self, handle: SandboxHandle) -> None:
        async with self._lock:
            self._require_open()
            record = self._records.get(handle)
            if record is None:
                raise SandboxNotFound(f"sandbox {handle} does not exist")
            task = self._start_deletion_locked(record)
        await _await_shared(task)

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is not None and close_task.done() and self._state is _ServiceState.CLOSED:
            return
        if close_task is None:
            close_task = asyncio.create_task(self._close_once())
            self._close_task = close_task
        await _await_shared(close_task)

    async def _close_once(self) -> None:
        async with self._lock:
            if self._state is _ServiceState.CLOSED:
                return
            self._state = _ServiceState.CLOSING
            pending = tuple(self._pending_operations)
            deletion_tasks = tuple(
                self._start_deletion_locked(record) for record in self._records.values()
            )
        if pending:
            await asyncio.gather(
                *(asyncio.shield(operation) for operation in pending),
                return_exceptions=True,
            )
        results = await asyncio.gather(
            *(asyncio.shield(task) for task in deletion_tasks),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        async with self._lock:
            self._state = _ServiceState.CLOSED
            self._reserved_handles.clear()
            self._reserved_session_ids.clear()
        if failures:
            primary = SandboxDeleteFailed("sandbox service cleanup failed")
            for failure in failures:
                primary.add_note(f"session cleanup failure: {failure}")
            raise primary

    async def _begin_lifecycle(self) -> asyncio.Future[None]:
        operation = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._require_open()
            self._pending_operations.add(operation)
        return operation

    async def _finish_lifecycle(
        self,
        operation: asyncio.Future[None],
        handle: SandboxHandle | None,
        session_id: SessionId | None,
    ) -> None:
        async with self._lock:
            self._pending_operations.discard(operation)
            if handle is not None:
                self._reserved_handles.discard(handle)
            if session_id is not None:
                self._reserved_session_ids.discard(session_id)
            if not operation.done():
                operation.set_result(None)

    async def _reserve_identifiers(self) -> tuple[SandboxHandle, SessionId]:
        try:
            handle = SandboxHandle(self._uuid_generator.new_uuid())
            session_id = SessionId(self._uuid_generator.new_uuid())
        except (TypeError, ValueError) as error:
            raise SandboxIdentifierConflict("generated sandbox identity is invalid") from error
        async with self._lock:
            self._require_open()
            if handle in self._reserved_handles or handle in self._records:
                raise SandboxIdentifierConflict(f"sandbox handle {handle} already exists")
            if session_id in self._reserved_session_ids or any(
                record.session_id == session_id for record in self._records.values()
            ):
                raise SandboxIdentifierConflict(f"session identifier {session_id} already exists")
            self._reserved_handles.add(handle)
            self._reserved_session_ids.add(session_id)
        return handle, session_id

    async def _publish(
        self,
        handle: SandboxHandle,
        owner_id: OwnerId,
        session_id: SessionId,
        runtime: ServiceSessionRuntime,
    ) -> None:
        created_at = _utc_now(self._clock)
        async with self._lock:
            self._require_open()
            self._records[handle] = _SessionRecord(
                handle=handle,
                owner_id=owner_id,
                session_id=session_id,
                runtime=runtime,
                created_at=created_at,
            )
            self._reserved_handles.discard(handle)
            self._reserved_session_ids.discard(session_id)

    def _start_deletion_locked(self, record: _SessionRecord) -> asyncio.Task[None]:
        if record.deletion_task is None:
            record.status = _RecordStatus.DELETING
            record.deletion_task = asyncio.create_task(self._delete_record(record))
        return record.deletion_task

    async def _delete_record(self, record: _SessionRecord) -> None:
        failure: BaseException | None = None
        try:
            await record.runtime.close()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            failure = error
        finally:
            async with self._lock:
                if self._records.get(record.handle) is record:
                    del self._records[record.handle]
                self._reserved_handles.discard(record.handle)
                self._reserved_session_ids.discard(record.session_id)
        if failure is not None:
            raise SandboxDeleteFailed(f"sandbox {record.handle} cleanup failed") from failure

    def _require_open(self) -> None:
        if self._state is not _ServiceState.OPEN:
            raise SandboxServiceClosed("sandbox service is closed")


def _utc_now(clock: Clock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise SandboxStartupFailed("service clock must return an aware datetime")
    if value.utcoffset() != timedelta(0):
        raise SandboxStartupFailed("service clock must return UTC")
    return value


async def _cleanup_after_failure(
    runtime: ServiceSessionRuntime,
    primary: BaseException | None,
) -> None:
    try:
        await runtime.close()
    except asyncio.CancelledError:
        raise
    except BaseException as cleanup_error:
        if primary is None:
            raise
        primary.add_note(f"secondary runtime cleanup failure: {cleanup_error}")


async def _await_shared(task: asyncio.Task[None]) -> None:
    if task.done():
        await task
        return
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise
