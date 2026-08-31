"""Parser and applicator for the constrained workspace unified-diff format."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from mem_sandbox.core.errors import SandboxError
from mem_sandbox.workspace.errors import InvalidPatchError, PatchContextMismatchError
from mem_sandbox.workspace.paths import SandboxPath
from mem_sandbox.workspace.text import split_normalized_lines

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: .*)?$"
)


@dataclass(frozen=True, slots=True)
class PatchLine:
    """One context, removal, or addition line."""

    prefix: str
    content: str


@dataclass(frozen=True, slots=True)
class PatchHunk:
    """One validated unified-diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[PatchLine, ...]


@dataclass(frozen=True, slots=True)
class FilePatch:
    """All hunks targeting one existing UTF-8 file."""

    path: SandboxPath
    hunks: tuple[PatchHunk, ...]


def parse_unified_diff(
    patch: str,
    resolve_path: Callable[[str], SandboxPath],
) -> tuple[FilePatch, ...]:
    """Parse the supported multi-file unified-diff subset."""
    normalized = patch.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise InvalidPatchError("patch must contain at least one file section")

    files: list[FilePatch] = []
    seen_paths: set[SandboxPath] = set()
    index = 0

    while index < len(lines):
        if not lines[index].startswith("--- "):
            raise InvalidPatchError("file section must begin with a --- header")
        old_path = _parse_header_path(lines[index], "--- ", resolve_path)
        index += 1

        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise InvalidPatchError("--- header must be followed by a +++ header")
        new_path = _parse_header_path(lines[index], "+++ ", resolve_path)
        index += 1

        if old_path != new_path:
            raise InvalidPatchError("file rename, creation, and deletion are unsupported")
        if old_path in seen_paths:
            raise InvalidPatchError(f"patch contains duplicate file section for {old_path}")

        hunks: list[PatchHunk] = []
        while index < len(lines) and lines[index].startswith("@@"):
            match = _HUNK_HEADER.fullmatch(lines[index])
            if match is None:
                raise InvalidPatchError("invalid unified-diff hunk header")
            old_start = int(match.group("old_start"))
            old_count = int(match.group("old_count") or "1")
            new_start = int(match.group("new_start"))
            new_count = int(match.group("new_count") or "1")
            _validate_hunk_range(old_start, old_count, new_start, new_count)
            index += 1

            hunk_lines: list[PatchLine] = []
            old_seen = 0
            new_seen = 0
            has_change = False
            while old_seen < old_count or new_seen < new_count:
                if index >= len(lines):
                    raise InvalidPatchError("patch hunk ended before its declared line counts")
                line = lines[index]
                if line == r"\ No newline at end of file":
                    index += 1
                    continue
                if not line or line[0] not in (" ", "-", "+"):
                    raise InvalidPatchError("patch hunk contains an unsupported line")

                prefix = line[0]
                hunk_lines.append(PatchLine(prefix, line[1:]))
                if prefix in (" ", "-"):
                    old_seen += 1
                if prefix in (" ", "+"):
                    new_seen += 1
                if prefix in ("-", "+"):
                    has_change = True
                if old_seen > old_count or new_seen > new_count:
                    raise InvalidPatchError("patch hunk exceeds its declared line counts")
                index += 1

            if not has_change:
                raise InvalidPatchError("patch hunk must contain at least one change")
            hunks.append(
                PatchHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=tuple(hunk_lines),
                )
            )

        if not hunks:
            raise InvalidPatchError("file section must contain at least one hunk")
        if index < len(lines) and not lines[index].startswith("--- "):
            raise InvalidPatchError("unexpected content between patch file sections")

        files.append(FilePatch(path=old_path, hunks=tuple(hunks)))
        seen_paths.add(old_path)

    return tuple(files)


def apply_file_patch(content: str, file_patch: FilePatch) -> str:
    """Apply parsed hunks against normalized LF text."""
    source_lines, had_trailing_newline = split_normalized_lines(content)

    result: list[str] = []
    source_index = 0

    for hunk in file_patch.hunks:
        target_index = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
        expected_new_index = hunk.new_start if hunk.new_count == 0 else hunk.new_start - 1
        if target_index < source_index or target_index > len(source_lines):
            raise PatchContextMismatchError(
                f"patch hunk for {file_patch.path} targets an invalid or overlapping range"
            )

        result.extend(source_lines[source_index:target_index])
        if len(result) != expected_new_index:
            raise InvalidPatchError(
                f"patch hunk for {file_patch.path} has inconsistent new line numbers"
            )
        source_index = target_index

        for line in hunk.lines:
            if line.prefix == "+":
                result.append(line.content)
                continue
            if source_index >= len(source_lines):
                raise PatchContextMismatchError(
                    f"patch context for {file_patch.path} extends beyond end of file"
                )
            actual = source_lines[source_index]
            if actual != line.content:
                raise PatchContextMismatchError(
                    f"patch context for {file_patch.path} does not match at line {source_index + 1}"
                )
            if line.prefix == " ":
                result.append(actual)
            source_index += 1

    result.extend(source_lines[source_index:])
    patched = "\n".join(result)
    if had_trailing_newline:
        patched += "\n"
    return patched


def _parse_header_path(
    line: str,
    prefix: str,
    resolve_path: Callable[[str], SandboxPath],
) -> SandboxPath:
    raw_path = line[len(prefix) :].split("\t", 1)[0]
    if not raw_path:
        raise InvalidPatchError("patch file header path must not be empty")
    if raw_path == "/dev/null":
        raise InvalidPatchError("file creation and deletion patches are unsupported")
    if raw_path.startswith(("a/", "b/")):
        raw_path = raw_path[2:]
    try:
        return resolve_path(raw_path)
    except (SandboxError, TypeError, ValueError) as error:
        raise InvalidPatchError("patch contains an invalid workspace path") from error


def _validate_hunk_range(
    old_start: int,
    old_count: int,
    new_start: int,
    new_count: int,
) -> None:
    if old_count < 0 or new_count < 0:
        raise InvalidPatchError("patch hunk counts must be non-negative")
    if old_start < (0 if old_count == 0 else 1):
        raise InvalidPatchError("patch hunk old range is invalid")
    if new_start < (0 if new_count == 0 else 1):
        raise InvalidPatchError("patch hunk new range is invalid")
