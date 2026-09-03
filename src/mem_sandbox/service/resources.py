"""Idempotent service-owned resource cleanup."""

from __future__ import annotations

import asyncio

from mem_sandbox.service.errors import SessionFactoryCleanupFailed
from mem_sandbox.service.ports import AsyncCloseable
from mem_sandbox.session import SandboxSession


class CompositeResourceScope:
    """Close owned resources once in reverse construction order."""

    def __init__(self, resources: tuple[AsyncCloseable, ...] = ()) -> None:
        self._resources = resources
        self._close_task: asyncio.Task[None] | None = None

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(self._close_once())
            self._close_task = close_task
        await _await_shared(close_task)

    async def close_with_timeout(self, timeout_seconds: float) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(self._close_once())
            self._close_task = close_task
        try:
            async with asyncio.timeout(timeout_seconds):
                await asyncio.shield(close_task)
        except TimeoutError:
            close_task.cancel()
            try:
                await close_task
            except asyncio.CancelledError:
                pass
            raise

    async def _close_once(self) -> None:
        primary: BaseException | None = None
        cancellation: asyncio.CancelledError | None = None
        for resource in reversed(self._resources):
            try:
                await resource.close()
            except asyncio.CancelledError as error:
                cancellation = error
            except BaseException as error:
                if primary is None:
                    primary = error
                else:
                    primary.add_note(f"secondary cleanup failure: {error}")
        if cancellation is not None:
            raise cancellation
        if primary is not None:
            failure = SessionFactoryCleanupFailed("owned resource cleanup failed")
            for note in getattr(primary, "__notes__", ()):
                failure.add_note(note)
            raise failure from primary


class DefaultServiceSessionRuntime:
    """Own a session and resources that must outlive its final event."""

    def __init__(
        self,
        session: SandboxSession,
        post_session_scope: CompositeResourceScope,
        *,
        timeout_seconds: float,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._session = session
        self._post_session_scope = post_session_scope
        self._timeout_seconds = timeout_seconds
        self._close_task: asyncio.Task[None] | None = None

    @property
    def session(self) -> SandboxSession:
        return self._session

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(self._close_once())
            self._close_task = close_task
        await _await_shared(close_task)

    async def _close_once(self) -> None:
        primary: BaseException | None = None
        try:
            await self._session.close()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            primary = error
        try:
            await self._post_session_scope.close_with_timeout(self._timeout_seconds)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                primary.add_note(f"secondary post-session cleanup failure: {error}")
        if primary is not None:
            raise primary


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
