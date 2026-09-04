from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from types import TracebackType
from typing import Any, Self, TypeVar

from hypothesis.stateful import RuleBasedStateMachine

T = TypeVar("T")


class AsyncRunner:
    """Own one reusable asyncio runner for a generated example or state machine."""

    def __init__(self) -> None:
        self._runner = asyncio.Runner()

    def run(self, operation: Coroutine[Any, Any, T]) -> T:
        return self._runner.run(operation)

    def close(self) -> None:
        self._runner.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncRuleBasedStateMachine(RuleBasedStateMachine):
    """Rule-based machine with exactly one asyncio runner for its lifetime."""

    def __init__(self) -> None:
        super().__init__()
        self._async_runner = AsyncRunner()

    def run_async(self, operation: Coroutine[Any, Any, T]) -> T:
        return self._async_runner.run(operation)

    def teardown(self) -> None:
        self._async_runner.close()
        super().teardown()
