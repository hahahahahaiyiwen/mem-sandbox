"""Fail-closed and bounded process-local secret brokers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

from mem_sandbox.core import Clock
from mem_sandbox.secrets.errors import (
    SecretDenied,
    SecretExpired,
    SecretLeaseCleanupFailed,
    SecretLeaseClosed,
    SecretLeaseLimitExceeded,
    SecretNotFound,
    SecretSourceUnavailable,
    SecretSourceValueInvalid,
)
from mem_sandbox.secrets.models import (
    SecretAccessRequest,
    SecretBrokerLimits,
    SecretLease,
    SecretSource,
    SecretValue,
)


class NoSecretBroker:
    """Reject every lease request explicitly."""

    __slots__ = ()

    async def lease(self, request: SecretAccessRequest) -> SecretLease:
        raise SecretDenied(f"secret access is unsupported for reference {request.secret_ref.name}")


class BoundedSecretBroker:
    """Resolve values into independent finite leases with atomic capacity admission."""

    def __init__(
        self,
        source: SecretSource,
        *,
        limits: SecretBrokerLimits,
        clock: Clock,
    ) -> None:
        if not callable(getattr(source, "resolve", None)):
            raise TypeError("source must implement resolve")
        if not isinstance(cast(object, limits), SecretBrokerLimits):
            raise TypeError("limits must be SecretBrokerLimits")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock must implement now")
        self._source = source
        self._limits = limits
        self._clock = clock
        self._lock = asyncio.Lock()
        self._records: dict[int, datetime] = {}
        self._next_token = 1

    async def lease(self, request: SecretAccessRequest) -> SecretLease:
        if not isinstance(cast(object, request), SecretAccessRequest):
            raise TypeError("request must be a SecretAccessRequest")
        async with self._lock:
            accepted_at = self._now()
            self._purge_expired_locked(accepted_at)
            if len(self._records) >= self._limits.max_active_leases:
                raise SecretLeaseLimitExceeded(
                    "accepting the secret lease would exceed the configured active-lease limit"
                )
            duration = min(
                request.max_lease_seconds,
                self._limits.max_lease_seconds,
            )
            try:
                expires_at = accepted_at + timedelta(seconds=duration)
            except OverflowError:
                raise SecretSourceUnavailable(
                    "secret lease expiration exceeds the supported datetime range"
                ) from None
            token = self._next_token
            self._next_token += 1
            self._records[token] = expires_at

        try:
            value, failure = await self._resolve(request)
            if failure is not None:
                raise failure
            if value is None:
                raise SecretSourceUnavailable(
                    f"secret source returned no value for reference {request.secret_ref.name}"
                )
            encoding_failed = False
            try:
                value_bytes = value.reveal_bytes()
            except UnicodeEncodeError:
                encoding_failed = True
                value_bytes = b""
            if encoding_failed:
                raise SecretSourceValueInvalid(
                    f"secret source value for reference {request.secret_ref.name} "
                    "is not valid UTF-8"
                )
            if not value_bytes or len(value_bytes) > self._limits.max_value_bytes:
                raise SecretSourceValueInvalid(
                    f"secret source value for reference {request.secret_ref.name} "
                    "is outside the configured UTF-8 byte bounds"
                )
            lease = _BoundedSecretLease(
                value=value,
                expires_at=expires_at,
                now=self._now,
                release=lambda: self._release(token),
            )
        except BaseException:
            self._release(token)
            raise
        return lease

    async def _resolve(
        self,
        request: SecretAccessRequest,
    ) -> tuple[SecretValue | None, Exception | None]:
        try:
            value = await self._source.resolve(request.secret_ref)
        except asyncio.CancelledError:
            raise
        except SecretNotFound:
            return None, SecretNotFound(
                f"secret reference {request.secret_ref.name} does not exist"
            )
        except SecretSourceValueInvalid:
            return None, SecretSourceValueInvalid(
                f"secret source value for reference {request.secret_ref.name} is invalid"
            )
        except SecretSourceUnavailable:
            return None, SecretSourceUnavailable(
                f"secret source is unavailable for reference {request.secret_ref.name}"
            )
        except Exception:
            return None, SecretSourceUnavailable(
                f"secret source is unavailable for reference {request.secret_ref.name}"
            )
        if not isinstance(cast(object, value), SecretValue):
            return None, SecretSourceValueInvalid(
                f"secret source returned an invalid value for reference {request.secret_ref.name}"
            )
        return value, None

    def _release(self, token: int) -> None:
        self._records.pop(token, None)

    def _purge_expired_locked(self, now: datetime) -> None:
        for token in tuple(
            token for token, expires_at in self._records.items() if expires_at <= now
        ):
            self._records.pop(token, None)

    def _now(self) -> datetime:
        failure = False
        try:
            now = self._clock.now()
        except Exception:
            failure = True
            now = datetime.min
        if failure:
            raise SecretSourceUnavailable("secret broker clock is unavailable")
        if not isinstance(cast(object, now), datetime):
            raise SecretSourceUnavailable("secret broker clock must return a datetime")
        try:
            offset = now.utcoffset()
        except Exception:
            raise SecretSourceUnavailable("secret broker clock returned an invalid datetime") from None
        if now.tzinfo is None or offset is None:
            raise SecretSourceUnavailable("secret broker clock must return an aware datetime")
        if offset != timedelta(0):
            raise SecretSourceUnavailable("secret broker clock must return UTC")
        return now


class _BoundedSecretLease:
    """One non-revealing lease backed by a broker capacity record."""

    __slots__ = (
        "_close_lock",
        "_closed",
        "_expires_at",
        "_now",
        "_release",
        "_value",
    )

    def __init__(
        self,
        *,
        value: SecretValue,
        expires_at: datetime,
        now: Callable[[], datetime],
        release: Callable[[], None],
    ) -> None:
        self._value = value
        self._expires_at = expires_at
        self._now = now
        self._release = release
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def value(self) -> SecretValue:
        if self._closed:
            raise SecretLeaseClosed("secret lease is closed")
        now = self._now()
        if now >= self._expires_at:
            raise SecretExpired("secret lease is expired")
        return self._value

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    async def close(self) -> None:
        if self._closed:
            return
        failure: SecretLeaseCleanupFailed | None = None
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._release()
            except Exception:
                failure = SecretLeaseCleanupFailed("secret lease cleanup failed")
        if failure is not None:
            raise failure
