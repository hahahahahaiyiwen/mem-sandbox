"""UTF-8-safe bounded stream collection."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoundedText:
    """A retained UTF-8 prefix and observable original size."""

    text: str
    original_bytes: int
    truncated: bool


class BoundedOutputCollector:
    """Collect text while retaining only complete UTF-8 code points."""

    def __init__(self, max_bytes: int) -> None:
        _require_max_bytes(max_bytes)
        self._max_bytes = max_bytes
        self._parts: list[str] = []
        self._retained_bytes = 0
        self._original_bytes = 0
        self._truncated = False
        self._retention_closed = False

    def append(self, text: str) -> None:
        """Append emitted text and retain the bounded UTF-8 prefix."""
        _require_text(text)
        encoded = text.encode("utf-8")
        self._original_bytes += len(encoded)
        if self._retention_closed:
            self._truncated = self._truncated or bool(encoded)
            return
        remaining = self._max_bytes - self._retained_bytes
        if remaining <= 0:
            self._truncated = self._truncated or bool(encoded)
            self._retention_closed = bool(encoded)
            return
        if len(encoded) <= remaining:
            self._parts.append(text)
            self._retained_bytes += len(encoded)
            return
        prefix = encoded[:remaining]
        while prefix:
            try:
                retained = prefix.decode("utf-8")
                break
            except UnicodeDecodeError as error:
                prefix = prefix[: error.start]
        else:
            retained = ""
        self._parts.append(retained)
        self._retained_bytes += len(prefix)
        self._truncated = True
        self._retention_closed = True

    def result(self) -> BoundedText:
        """Materialize the immutable collector result."""
        return BoundedText("".join(self._parts), self._original_bytes, self._truncated)


def bound_text(text: str, max_bytes: int) -> BoundedText:
    """Bound one stage output independently."""
    collector = BoundedOutputCollector(max_bytes)
    collector.append(text)
    return collector.result()


def _require_max_bytes(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_bytes must be an integer")
    if value <= 0:
        raise ValueError("max_bytes must be positive")


def _require_text(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError("text must be a string")
