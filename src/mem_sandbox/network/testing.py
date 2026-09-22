"""Deterministic testing support for outbound HTTP gateway implementations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TypeVar, cast

from mem_sandbox.core import SandboxError
from mem_sandbox.network.errors import (
    OutboundHttpCancelled,
    OutboundHttpGatewayFailed,
)
from mem_sandbox.network.models import (
    NetworkOperationContext,
    OutboundHttpRequest,
    OutboundHttpResponse,
)
from mem_sandbox.network.ports import OutboundHttpGateway

_ErrorT = TypeVar("_ErrorT", bound=SandboxError)


@dataclass(frozen=True, slots=True)
class FakeOutboundHttpCall:
    request: OutboundHttpRequest
    context: NetworkOperationContext


class FakeOutboundHttpGateway:
    """Return scripted bounded outcomes without opening a network connection."""

    def __init__(
        self,
        outcomes: tuple[OutboundHttpResponse | BaseException, ...],
        *,
        release: asyncio.Event | None = None,
    ) -> None:
        outcomes_value = cast(object, outcomes)
        if not isinstance(outcomes_value, tuple):
            raise TypeError("outcomes must be a tuple")
        raw_outcomes = cast(tuple[object, ...], outcomes_value)
        if any(
            not isinstance(outcome, OutboundHttpResponse | BaseException)
            for outcome in raw_outcomes
        ):
            raise TypeError("outcomes must contain responses or exceptions")
        self._outcomes = outcomes
        self._release = release
        self._calls: list[FakeOutboundHttpCall] = []
        self.entered = asyncio.Event()
        self.close_count = 0

    @property
    def calls(self) -> tuple[FakeOutboundHttpCall, ...]:
        return tuple(self._calls)

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        context.grant.require_request(request)
        index = len(self._calls)
        self._calls.append(FakeOutboundHttpCall(request, context))
        self.entered.set()
        await self._await_release(context)
        if index >= len(self._outcomes):
            raise OutboundHttpGatewayFailed("fake gateway has no scripted outcome")
        outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        context.grant.require_response(request, outcome)
        return outcome

    async def _await_release(self, context: NetworkOperationContext) -> None:
        release = self._release
        if release is None:
            _require_not_cancelled(context)
            return
        while not release.is_set():
            _require_not_cancelled(context)
            try:
                await asyncio.wait_for(release.wait(), timeout=0.01)
            except TimeoutError:
                pass
        _require_not_cancelled(context)


class OutboundHttpGatewayConformanceDriver:
    """Reusable behavior probes shared by fake and transport implementations."""

    def __init__(self, gateway: OutboundHttpGateway) -> None:
        self._gateway = gateway

    async def assert_round_trip(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
        expected: OutboundHttpResponse,
    ) -> OutboundHttpResponse:
        actual = await self._gateway.send(request, context)
        if actual != expected:
            raise AssertionError(f"gateway response {actual!r} did not match expected value")
        return actual

    async def assert_stable_failure(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
        error_type: type[_ErrorT],
    ) -> _ErrorT:
        try:
            await self._gateway.send(request, context)
        except error_type as error:
            return error
        except BaseException as error:
            raise AssertionError(
                f"gateway raised {type(error).__name__}, expected {error_type.__name__}"
            ) from error
        raise AssertionError(f"gateway did not raise {error_type.__name__}")


def _require_not_cancelled(context: NetworkOperationContext) -> None:
    cancellation = context.cancellation
    if cancellation is not None and cancellation.is_set():
        raise OutboundHttpCancelled("outbound HTTP operation was cancelled")
