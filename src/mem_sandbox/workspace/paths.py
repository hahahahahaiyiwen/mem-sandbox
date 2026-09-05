"""Host-independent POSIX workspace paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Self

from mem_sandbox.workspace.errors import InvalidPathError, PathOutsideWorkspaceError


@dataclass(frozen=True, slots=True, order=True)
class SandboxPath:
    """A normalized, root-confined POSIX path."""

    ROOT: ClassVar[str] = "/workspace"

    value: str

    def __post_init__(self) -> None:
        _require_text("path value", self.value)
        if self.value != self._canonical_absolute(self.value):
            raise InvalidPathError("SandboxPath value must already be canonical")

    @classmethod
    def root(cls) -> Self:
        """Return the canonical workspace root."""
        return cls(cls.ROOT)

    @classmethod
    def resolve(
        cls,
        value: str,
        *,
        cwd: SandboxPath | None = None,
        max_path_bytes: int = 4_096,
        max_segment_bytes: int = 255,
    ) -> Self:
        """Normalize an absolute or cwd-relative untrusted path."""
        _require_text("path value", value)
        if not value:
            raise InvalidPathError("path must not be empty")
        if "\x00" in value:
            raise InvalidPathError("path must not contain NUL")
        _require_positive_limit("max_path_bytes", max_path_bytes)
        _require_positive_limit("max_segment_bytes", max_segment_bytes)

        if value.startswith("/"):
            parts: list[str] = []
        else:
            if cwd is None:
                raise InvalidPathError("relative path requires an explicit working directory")
            _require_path("cwd", cwd)
            parts = ["workspace", *cwd.parts]

        for segment in value.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                raise InvalidPathError("parent traversal is not allowed")
            if "\x00" in segment:
                raise InvalidPathError("path segment must not contain NUL")
            parts.append(segment)

        if not parts or parts[0] != "workspace":
            raise PathOutsideWorkspaceError(f"path must be contained by {cls.ROOT}")

        normalized = "/" + "/".join(parts)
        if normalized != cls.ROOT and not normalized.startswith(f"{cls.ROOT}/"):
            raise PathOutsideWorkspaceError(f"path must be contained by {cls.ROOT}")

        for segment in parts:
            segment_bytes = _utf8_length("path segment", segment)
            if segment_bytes > max_segment_bytes:
                raise InvalidPathError(f"path segment exceeds {max_segment_bytes} UTF-8 bytes")

        path_bytes = _utf8_length("path", normalized)
        if path_bytes > max_path_bytes:
            raise InvalidPathError(f"path exceeds {max_path_bytes} UTF-8 bytes")

        return cls(normalized)

    @property
    def is_root(self) -> bool:
        """Return whether this path is the workspace root."""
        return self.value == self.ROOT

    @property
    def parts(self) -> tuple[str, ...]:
        """Return path segments relative to the workspace root."""
        if self.is_root:
            return ()
        return tuple(self.value[len(self.ROOT) + 1 :].split("/"))

    @property
    def name(self) -> str:
        """Return the final segment, or ``workspace`` for the root."""
        return "workspace" if self.is_root else self.parts[-1]

    @property
    def parent(self) -> Self:
        """Return the parent path; the root is its own parent."""
        if self.is_root:
            return self
        parent_parts = self.parts[:-1]
        if not parent_parts:
            return type(self).root()
        return type(self)(f"{self.ROOT}/{'/'.join(parent_parts)}")

    def join(
        self,
        *segments: str,
        max_path_bytes: int = 4_096,
        max_segment_bytes: int = 255,
    ) -> Self:
        """Resolve child segments under this path."""
        if not segments:
            return self
        return type(self).resolve(
            "/".join(segments),
            cwd=self,
            max_path_bytes=max_path_bytes,
            max_segment_bytes=max_segment_bytes,
        )

    def is_ancestor_of(self, other: SandboxPath) -> bool:
        """Return whether this path strictly contains another path."""
        _require_path("other", other)
        return other.value.startswith(f"{self.value}/")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def _canonical_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            return ""
        if value == cls.ROOT:
            return value
        if not value.startswith(f"{cls.ROOT}/"):
            return ""
        if "//" in value or "/./" in value or value.endswith("/.") or value.endswith("/"):
            return ""
        if any(segment in ("", ".", "..") for segment in value.split("/")[1:]):
            return ""
        if "\x00" in value:
            return ""
        return value


def _require_positive_limit(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _require_path(name: str, value: object) -> SandboxPath:
    if not isinstance(value, SandboxPath):
        raise TypeError(f"{name} must be a SandboxPath")
    return value


def _utf8_length(name: str, value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise InvalidPathError(f"{name} must be valid UTF-8 text") from error
