import asyncio
from typing import cast

import pytest

from mem_sandbox.service import (
    CompositeResourceScope,
    DefaultServiceSessionRuntime,
    SessionFactoryCleanupFailed,
)
from mem_sandbox.session import SandboxSession


class RecordingResource:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        fail: bool = False,
        entered: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail
        self.entered = entered
        self.release = release
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        self.calls.append(self.name)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError(self.name)


@pytest.mark.asyncio
async def test_composite_scope_closes_in_reverse_once_and_reports_secondary_failures() -> None:
    calls: list[str] = []
    first = RecordingResource("first", calls, fail=True)
    second = RecordingResource("second", calls, fail=True)
    scope = CompositeResourceScope((first, second))

    with pytest.raises(SessionFactoryCleanupFailed) as captured:
        await scope.close()
    with pytest.raises(SessionFactoryCleanupFailed):
        await scope.close()

    assert calls == ["second", "first"]
    assert first.close_count == second.close_count == 1
    assert captured.value.__notes__ == ["secondary cleanup failure: first"]


@pytest.mark.asyncio
async def test_concurrent_scope_close_waiters_share_one_cleanup() -> None:
    calls: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()
    resource = RecordingResource("resource", calls, entered=entered, release=release)
    scope = CompositeResourceScope((resource,))

    first = asyncio.create_task(scope.close())
    await entered.wait()
    second = asyncio.create_task(scope.close())
    release.set()
    await asyncio.gather(first, second)

    assert resource.close_count == 1


@pytest.mark.asyncio
async def test_runtime_keeps_post_session_resources_until_session_close_finishes() -> None:
    calls: list[str] = []
    session_resource = RecordingResource("session", calls)
    post_resource = RecordingResource("post", calls)
    runtime = DefaultServiceSessionRuntime(
        cast(SandboxSession, session_resource),
        CompositeResourceScope((post_resource,)),
        timeout_seconds=1,
    )

    await runtime.close()
    await runtime.close()

    assert calls == ["session", "post"]
    assert session_resource.close_count == post_resource.close_count == 1


@pytest.mark.asyncio
async def test_runtime_timeout_cancels_cooperative_post_session_cleanup() -> None:
    calls: list[str] = []
    post_started = asyncio.Event()

    class CooperativePostResource:
        async def close(self) -> None:
            calls.append("post")
            post_started.set()
            await asyncio.sleep(10)

    runtime = DefaultServiceSessionRuntime(
        cast(SandboxSession, RecordingResource("session", calls)),
        CompositeResourceScope((CooperativePostResource(),)),
        timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError):
        await runtime.close()

    assert post_started.is_set()
    assert calls == ["session", "post"]
