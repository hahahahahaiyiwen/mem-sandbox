"""Structural protected-value redaction and payload preparation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

from mem_sandbox.events.errors import EventPayloadRejected
from mem_sandbox.events.models import (
    EventAttribute,
    EventPayloadLimits,
    EventPayloadPolicy,
    EventSensitivity,
    SandboxEvent,
    canonical_event_bytes,
)

_REDACTION_TEXT = "[REDACTED]"
_REDACTION_BYTES = _REDACTION_TEXT.encode()


class ProtectedValueRedactor:
    """Redact explicitly registered text and byte values in one pass."""

    def __init__(
        self,
        *,
        text_values: Iterable[str] = (),
        byte_values: Iterable[bytes] = (),
    ) -> None:
        text_registered: set[str] = set()
        byte_registered: set[bytes] = set()

        for value in text_values:
            text_value = cast(object, value)
            if not isinstance(text_value, str):
                raise TypeError("protected text values must be strings")
            if not text_value:
                raise ValueError("protected registrations must not be empty")
            text_registered.add(text_value)
            byte_registered.add(text_value.encode("utf-8"))
        for value in byte_values:
            byte_value = cast(object, value)
            if not isinstance(byte_value, bytes):
                raise TypeError("protected byte values must be bytes")
            if not byte_value:
                raise ValueError("protected registrations must not be empty")
            byte_registered.add(byte_value)
            try:
                text_registered.add(byte_value.decode("utf-8"))
            except UnicodeDecodeError:
                pass

        self._text_values = tuple(
            sorted(text_registered, key=lambda item: (-len(item), item))
        )
        self._byte_values = tuple(
            sorted(byte_registered, key=lambda item: (-len(item), item))
        )

    def redact_text(self, value: str) -> str:
        """Redact all registered text matches without rescanning replacements."""
        text_value = cast(object, value)
        if not isinstance(text_value, str):
            raise TypeError("value must be a string")
        return _redact_text_spans(text_value, self._text_values)

    def redact_bytes(self, value: bytes) -> bytes:
        """Redact all registered byte matches without decoding the input."""
        byte_value = cast(object, value)
        if not isinstance(byte_value, bytes):
            raise TypeError("value must be bytes")
        return _redact_byte_spans(byte_value, self._byte_values)

    def redact_event(self, event: SandboxEvent) -> SandboxEvent:
        """Return an immutable event with structural string redaction applied."""
        redacted_attributes: list[EventAttribute] = []
        for attribute in event.attributes:
            if not isinstance(attribute.value, str):
                redacted_attributes.append(attribute)
                continue
            redacted_value = self.redact_text(attribute.value)
            if (
                attribute.sensitivity is EventSensitivity.PROTECTED
                and redacted_value != attribute.value
            ):
                redacted_value = _REDACTION_TEXT
            redacted_attributes.append(replace(attribute, value=redacted_value))
        attributes = tuple(redacted_attributes)
        if attributes == event.attributes:
            return event
        return replace(event, attributes=attributes)


def prepare_event(
    event: SandboxEvent,
    *,
    redactor: ProtectedValueRedactor,
    payload_policy: EventPayloadPolicy,
    limits: EventPayloadLimits,
) -> SandboxEvent:
    """Classify, redact, and validate an event before a sink can observe it."""
    if len(event.attributes) > limits.max_attributes:
        raise EventPayloadRejected(
            f"event payload exceeds {limits.max_attributes} attributes"
        )
    for attribute in event.attributes:
        if attribute.sensitivity is EventSensitivity.SECRET:
            raise EventPayloadRejected("secret event attributes are not allowed")
        if (
            attribute.sensitivity is EventSensitivity.PROTECTED
            and not payload_policy.allow_protected
        ):
            raise EventPayloadRejected(
                "protected event attributes require explicit payload-policy opt-in"
            )

    prepared = redactor.redact_event(event)
    for original, redacted in zip(
        event.attributes,
        prepared.attributes,
        strict=True,
    ):
        if original.sensitivity is not EventSensitivity.PROTECTED:
            continue
        if (
            not isinstance(original.value, str)
            or original.value == redacted.value
        ):
            raise EventPayloadRejected(
                "protected event attributes must be structurally redacted"
            )
    _validate_field_limits(prepared.attributes, limits)
    if len(canonical_event_bytes(prepared)) > limits.max_event_bytes:
        raise EventPayloadRejected(
            f"canonical event payload exceeds {limits.max_event_bytes} UTF-8 bytes"
        )
    return prepared


def _validate_field_limits(
    attributes: tuple[EventAttribute, ...],
    limits: EventPayloadLimits,
) -> None:
    for attribute in attributes:
        if len(attribute.name.encode("utf-8")) > limits.max_attribute_name_bytes:
            raise EventPayloadRejected(
                "event attribute name exceeds "
                f"{limits.max_attribute_name_bytes} UTF-8 bytes"
            )
        if (
            isinstance(attribute.value, str)
            and len(attribute.value.encode("utf-8")) > limits.max_string_bytes
        ):
            raise EventPayloadRejected(
                f"event string value exceeds {limits.max_string_bytes} UTF-8 bytes"
            )


def _redact_text_spans(value: str, protected: tuple[str, ...]) -> str:
    spans = _text_match_spans(value, protected)
    if not spans:
        return value
    parts: list[str] = []
    cursor = 0
    for start, end in _merge_overlapping_spans(spans):
        parts.append(value[cursor:start])
        parts.append(_REDACTION_TEXT)
        cursor = end
    parts.append(value[cursor:])
    return "".join(parts)


def _redact_byte_spans(value: bytes, protected: tuple[bytes, ...]) -> bytes:
    spans = _byte_match_spans(value, protected)
    if not spans:
        return value
    parts: list[bytes] = []
    cursor = 0
    for start, end in _merge_overlapping_spans(spans):
        parts.append(value[cursor:start])
        parts.append(_REDACTION_BYTES)
        cursor = end
    parts.append(value[cursor:])
    return b"".join(parts)


def _text_match_spans(
    value: str,
    protected: tuple[str, ...],
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for candidate in protected:
        start = 0
        while (match := value.find(candidate, start)) >= 0:
            spans.append((match, match + len(candidate)))
            start = match + 1
    return spans


def _byte_match_spans(
    value: bytes,
    protected: tuple[bytes, ...],
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for candidate in protected:
        start = 0
        while (match := value.find(candidate, start)) >= 0:
            spans.append((match, match + len(candidate)))
            start = match + 1
    return spans


def _merge_overlapping_spans(
    spans: list[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    ordered = sorted(spans, key=lambda span: (span[0], -span[1]))
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start >= merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return tuple(merged)
