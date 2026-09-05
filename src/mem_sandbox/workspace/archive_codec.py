"""Deterministic bounded portable workspace tar codec."""

from __future__ import annotations

import gzip
import io
import tarfile
from collections.abc import Buffer
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol

from mem_sandbox.workspace.errors import (
    InvalidPathError,
    SnapshotCorruptError,
    SnapshotIncompatibleError,
    SnapshotTooLargeError,
)
from mem_sandbox.workspace.models import (
    DecodedWorkspaceTree,
    NodeKind,
    WorkspaceLimits,
    WorkspaceSnapshotEntry,
)
from mem_sandbox.workspace.paths import SandboxPath
from mem_sandbox.workspace.snapshot_tree import measure_workspace_entries

_CHUNK_BYTES = 64 * 1024
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_REGULAR_FILE_TYPES = (tarfile.REGTYPE, tarfile.AREGTYPE)
_UNSUPPORTED_EXTENSION_TYPES = (
    tarfile.GNUTYPE_LONGNAME,
    tarfile.GNUTYPE_LONGLINK,
    tarfile.GNUTYPE_SPARSE,
    tarfile.SOLARIS_XHDTYPE,
)


class _ByteReader(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _ScannedTarMember:
    raw_name: str
    effective_name: str


class _BoundedArchiveWriter(io.BytesIO):
    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self._max_bytes = max_bytes
        self._retained_bytes = 0

    @property
    def retained_bytes(self) -> int:
        return self._retained_bytes

    def write(self, buffer: Buffer, /) -> int:
        end = self.tell() + memoryview(buffer).nbytes
        if end > self._max_bytes:
            raise SnapshotTooLargeError(
                f"archive expands beyond the {self._max_bytes}-byte output limit"
            )
        written = super().write(buffer)
        self._retained_bytes = max(self._retained_bytes, self.tell())
        return written


class PortableWorkspaceArchiveCodec:
    """Portable archive profile version 1."""

    FORMAT_VERSION = 1

    def encode(
        self,
        entries: tuple[WorkspaceSnapshotEntry, ...],
        limits: WorkspaceLimits,
    ) -> bytes:
        """Encode immutable entries as canonical uncompressed POSIX PAX tar."""
        ordered = tuple(sorted(entries, key=lambda item: item.path.value))
        tree_stats = measure_workspace_entries(ordered)
        _validate_tree_limits(ordered, tree_stats.total_bytes, limits)

        output = _BoundedArchiveWriter(limits.max_snapshot_bytes)
        try:
            with tarfile.open(
                fileobj=output,
                mode="w",
                format=tarfile.PAX_FORMAT,
                encoding="utf-8",
                errors="strict",
            ) as archive:
                for entry in ordered:
                    info = tarfile.TarInfo("/".join(entry.path.parts))
                    if entry.kind is NodeKind.FILE:
                        assert entry.content is not None
                        content = entry.content
                        info.type = tarfile.REGTYPE
                        info.mode = 0o644
                    elif entry.kind is NodeKind.DIRECTORY:
                        content = b""
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                    else:
                        raise SnapshotCorruptError(
                            f"workspace entry has unsupported node kind: {entry.path}"
                        )
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.size = len(content) if entry.kind is NodeKind.FILE else 0
                    info.pax_headers = {}
                    archive.addfile(
                        info,
                        io.BytesIO(content) if entry.kind is NodeKind.FILE else None,
                    )
            encoded = output.getvalue()
        except SnapshotTooLargeError:
            raise
        except (OSError, UnicodeError, tarfile.TarError, ValueError) as error:
            raise SnapshotCorruptError("workspace entries could not be encoded as tar") from error
        finally:
            output.close()
        return encoded

    def decode(
        self,
        encoded: object,
        limits: WorkspaceLimits,
    ) -> DecodedWorkspaceTree:
        """Decode and validate bounded uncompressed or gzip-compressed tar bytes."""
        if not isinstance(encoded, bytes):
            raise TypeError("encoded archive must be bytes")
        if len(encoded) > limits.max_snapshot_bytes:
            raise SnapshotTooLargeError(
                f"archive contains {len(encoded)} input bytes; limit is "
                f"{limits.max_snapshot_bytes} bytes"
            )

        tar_bytes = _expand_archive(encoded, limits.max_snapshot_bytes)
        entries = self._decode_tar(tar_bytes, limits)
        tree_stats = measure_workspace_entries(entries)
        _validate_tree_limits(entries, tree_stats.total_bytes, limits)
        return DecodedWorkspaceTree(entries=entries, tree_stats=tree_stats)

    def _decode_tar(
        self,
        encoded: bytes,
        limits: WorkspaceLimits,
    ) -> tuple[WorkspaceSnapshotEntry, ...]:
        scanned_members = _scan_tar_members(encoded, limits)
        entries: dict[SandboxPath, WorkspaceSnapshotEntry] = {}
        synthetic_directories: set[SandboxPath] = set()
        explicit_paths: set[SandboxPath] = set()
        total_bytes = 0
        retained_path_bytes = 0
        member_count = 0
        root_marker_seen = False

        try:
            with tarfile.open(
                fileobj=io.BytesIO(encoded),
                mode="r:",
                encoding="utf-8",
                errors="strict",
            ) as archive:
                for member in archive:
                    member_count += 1
                    if member_count > limits.max_nodes:
                        raise SnapshotTooLargeError(
                            f"archive contains more than {limits.max_nodes} members"
                        )
                    _reject_sparse_member(member)
                    try:
                        scanned = scanned_members[member_count - 1]
                    except IndexError as error:
                        raise SnapshotCorruptError(
                            "archive parser returned an unexpected member"
                        ) from error
                    parsed_name = _canonical_member_name(member.name, member.isdir())
                    expected_name = _canonical_member_name(
                        scanned.effective_name,
                        member.isdir(),
                    )
                    if parsed_name != expected_name:
                        raise SnapshotCorruptError(
                            "archive parser-effective path does not match validated headers"
                        )
                    path = _member_path(scanned.effective_name, member.isdir(), limits)
                    if path is None:
                        if root_marker_seen:
                            raise SnapshotCorruptError("archive root marker is duplicated")
                        if member.size != 0:
                            raise SnapshotCorruptError(
                                "archive root marker directory has a non-zero size"
                            )
                        root_marker_seen = True
                        continue

                    retained_path_bytes = _ensure_parent_directories(
                        path,
                        entries,
                        synthetic_directories,
                        limits,
                        retained_path_bytes,
                    )

                    if member.isdir():
                        kind = NodeKind.DIRECTORY
                    elif member.type in _REGULAR_FILE_TYPES:
                        kind = NodeKind.FILE
                    else:
                        raise SnapshotCorruptError(
                            f"archive member has an unsupported type: {member.name}"
                        )

                    existing = entries.get(path)
                    if existing is not None:
                        if (
                            path in synthetic_directories
                            and kind is NodeKind.DIRECTORY
                            and path not in explicit_paths
                        ):
                            if member.size != 0:
                                raise SnapshotCorruptError(
                                    f"archive directory has a non-zero size: {member.name}"
                                )
                            synthetic_directories.remove(path)
                            explicit_paths.add(path)
                            continue
                        if existing.kind is not kind:
                            raise SnapshotCorruptError(
                                f"archive path has conflicting node types: {path}"
                            )
                        raise SnapshotCorruptError(f"archive path is duplicated: {path}")

                    if len(entries) + 2 > limits.max_nodes:
                        raise SnapshotTooLargeError(
                            f"archive tree contains more than {limits.max_nodes} nodes"
                        )
                    retained_path_bytes = _reserve_path_metadata(
                        path,
                        retained_path_bytes,
                        limits,
                    )

                    if kind is NodeKind.DIRECTORY:
                        if member.size != 0:
                            raise SnapshotCorruptError(
                                f"archive directory has a non-zero size: {member.name}"
                            )
                        entry = WorkspaceSnapshotEntry(path=path, kind=kind)
                    else:
                        if member.size < 0:
                            raise SnapshotCorruptError(
                                f"archive file has a negative size: {member.name}"
                            )
                        if member.size > limits.max_file_bytes:
                            raise SnapshotTooLargeError(
                                f"archive file {path} contains {member.size} bytes; limit is "
                                f"{limits.max_file_bytes} bytes"
                            )
                        next_total_bytes = total_bytes + member.size
                        if next_total_bytes > limits.max_total_bytes:
                            raise SnapshotTooLargeError(
                                f"archive files contain more than {limits.max_total_bytes} bytes"
                            )
                        file_data = archive.extractfile(member)
                        if file_data is None:
                            raise SnapshotCorruptError(
                                f"archive file payload is missing: {member.name}"
                            )
                        try:
                            content = _read_member(file_data, member.size)
                        finally:
                            file_data.close()
                        entry = WorkspaceSnapshotEntry(
                            path=path,
                            kind=NodeKind.FILE,
                            content=content,
                        )
                        total_bytes = next_total_bytes
                    entries[path] = entry
                    explicit_paths.add(path)
                    _check_node_limit(entries, limits)
                if member_count != len(scanned_members):
                    raise SnapshotCorruptError("archive parser omitted a validated physical member")
                _require_complete_tar(encoded, archive.offset)
        except (SnapshotCorruptError, SnapshotTooLargeError):
            raise
        except (EOFError, OSError, UnicodeError, tarfile.TarError, ValueError) as error:
            raise SnapshotCorruptError("archive is not a valid portable tar stream") from error

        return tuple(entries[path] for path in sorted(entries, key=lambda item: item.value))


def _expand_archive(encoded: bytes, max_bytes: int) -> bytes:
    if encoded.startswith(_GZIP_MAGIC):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(encoded), mode="rb") as archive:
                return _read_bounded(archive, max_bytes)
        except SnapshotTooLargeError:
            raise
        except (EOFError, OSError, gzip.BadGzipFile) as error:
            raise SnapshotCorruptError("archive is not valid gzip data") from error

    if (
        encoded.startswith(_BZIP2_MAGIC)
        or encoded.startswith(_XZ_MAGIC)
        or encoded.startswith(_ZSTD_MAGIC)
        or encoded.startswith(_ZIP_MAGICS)
    ) and not _starts_with_valid_tar_header(encoded):
        raise SnapshotIncompatibleError("archive compression profile is unsupported")
    return encoded


def _starts_with_valid_tar_header(encoded: bytes) -> bool:
    if len(encoded) < tarfile.BLOCKSIZE:
        return False
    try:
        tarfile.TarInfo.frombuf(
            encoded[: tarfile.BLOCKSIZE],
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeError, ValueError, tarfile.HeaderError):
        return False
    return True


def _read_bounded(stream: _ByteReader, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(_CHUNK_BYTES, max_bytes - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise SnapshotTooLargeError(f"archive expands beyond the {max_bytes}-byte limit")
        chunks.append(chunk)


def _read_member(stream: _ByteReader, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < expected_size:
        chunk = stream.read(min(_CHUNK_BYTES, expected_size - total))
        if not chunk:
            raise SnapshotCorruptError("archive file payload is truncated")
        chunks.append(chunk)
        total += len(chunk)
    content = b"".join(chunks)
    if len(content) != expected_size:
        raise SnapshotCorruptError("archive file size does not match its payload")
    return content


def _require_complete_tar(encoded: bytes, content_end: int) -> None:
    trailer = encoded[content_end:]
    required_end_bytes = 2 * tarfile.BLOCKSIZE
    if (
        content_end % tarfile.BLOCKSIZE != 0
        or len(encoded) % tarfile.BLOCKSIZE != 0
        or len(trailer) < required_end_bytes
        or any(trailer)
    ):
        raise SnapshotCorruptError(
            "archive must end with two zero blocks and zero-only block padding"
        )


def _scan_tar_members(
    encoded: bytes,
    limits: WorkspaceLimits,
) -> tuple[_ScannedTarMember, ...]:
    members: list[_ScannedTarMember] = []
    cursor = 0
    local_pax_pending = False
    local_pax_path: str | None = None
    global_pax_seen = False
    pax_record_count = 0

    while cursor + tarfile.BLOCKSIZE <= len(encoded):
        header = _header_block(encoded, cursor)
        if not any(header):
            if local_pax_pending:
                raise SnapshotCorruptError("archive local PAX header has no member")
            _require_complete_tar(encoded, cursor)
            return tuple(members)

        try:
            info = tarfile.TarInfo.frombuf(
                header,
                encoding="utf-8",
                errors="strict",
            )
        except (UnicodeError, ValueError, tarfile.HeaderError) as error:
            raise SnapshotCorruptError("archive contains a malformed physical header") from error
        if info.size < 0:
            raise SnapshotCorruptError("archive physical member has a negative size")

        payload_start = cursor + tarfile.BLOCKSIZE
        payload_end = payload_start + info.size
        next_header = payload_start + _padded_block_bytes(info.size)
        if payload_end > len(encoded) or next_header > len(encoded):
            raise SnapshotCorruptError("archive physical member payload is truncated")
        if any(encoded[payload_end:next_header]):
            raise SnapshotCorruptError("archive physical member padding must be zero-filled")

        if info.type == tarfile.XHDTYPE:
            if local_pax_pending:
                raise SnapshotCorruptError("archive contains stacked local PAX headers")
            local_pax_pending = True
            local_pax_path, parsed_records = _pax_path_from_payload(
                encoded[payload_start:payload_end],
                limits,
                limits.max_nodes - pax_record_count,
            )
            pax_record_count += parsed_records
        elif info.type == tarfile.XGLTYPE:
            if local_pax_pending:
                raise SnapshotCorruptError(
                    "archive global PAX header cannot follow a local PAX header"
                )
            if global_pax_seen:
                raise SnapshotCorruptError("archive contains multiple global PAX headers")
            global_path, parsed_records = _pax_path_from_payload(
                encoded[payload_start:payload_end],
                limits,
                limits.max_nodes - pax_record_count,
            )
            pax_record_count += parsed_records
            if global_path is not None:
                raise SnapshotCorruptError("archive global PAX path metadata is unsupported")
            global_pax_seen = True
        elif info.type in _UNSUPPORTED_EXTENSION_TYPES:
            raise SnapshotCorruptError(f"archive member uses an unsupported extension: {info.name}")
        else:
            raw_name = _raw_header_name(header)
            _validate_raw_header_name(
                raw_name,
                info.isdir(),
                allow_truncated_file_separator=local_pax_path is not None,
            )
            effective_name = local_pax_path if local_pax_path is not None else raw_name
            _canonical_member_name(effective_name, info.isdir())
            members.append(
                _ScannedTarMember(
                    raw_name=raw_name,
                    effective_name=effective_name,
                )
            )
            if len(members) > limits.max_nodes:
                raise SnapshotTooLargeError(
                    f"archive contains more than {limits.max_nodes} members"
                )
            local_pax_pending = False
            local_pax_path = None

        cursor = next_header

    raise SnapshotCorruptError("archive is missing complete end-of-archive blocks")


def _raw_header_name(header: bytes) -> str:
    name = _decode_header_path_field(header[:100])
    magic = header[257:265]
    if magic == tarfile.POSIX_MAGIC:
        prefix = _decode_header_path_field(header[345:500])
        return f"{prefix}/{name}" if prefix else name
    if magic == tarfile.GNU_MAGIC:
        prefix = _decode_header_path_field(header[345:500])
        if prefix:
            raise SnapshotCorruptError(
                "archive GNU header contains unsupported extended prefix data"
            )
        return name

    prefix = _decode_header_path_field(header[345:500])
    if prefix:
        raise SnapshotCorruptError("archive non-POSIX header contains unsupported prefix semantics")
    return name


def _pax_path_from_payload(
    payload: bytes,
    limits: WorkspaceLimits,
    max_records: int,
) -> tuple[str | None, int]:
    cursor = 0
    path: str | None = None
    record_count = 0
    while cursor < len(payload):
        record_count += 1
        if record_count > max_records:
            raise SnapshotTooLargeError(
                f"archive contains more than {limits.max_nodes} PAX records"
            )
        separator = payload.find(b" ", cursor)
        if separator < 0:
            raise SnapshotCorruptError("archive PAX record is missing its length separator")
        length_text = payload[cursor:separator]
        if not length_text or not length_text.isdigit():
            raise SnapshotCorruptError("archive PAX record has an invalid length")
        try:
            record_length = int(length_text)
        except ValueError as error:
            raise SnapshotCorruptError("archive PAX record length is invalid") from error
        record_end = cursor + record_length
        if record_end <= separator + 1 or record_end > len(payload):
            raise SnapshotCorruptError("archive PAX record length exceeds its payload")
        if payload[record_end - 1] != ord("\n"):
            raise SnapshotCorruptError("archive PAX record is not newline terminated")
        equals = payload.find(b"=", separator + 1, record_end - 1)
        if equals <= separator + 1:
            raise SnapshotCorruptError("archive PAX record is not a key-value assignment")
        try:
            key = payload[separator + 1 : equals].decode("utf-8")
        except UnicodeDecodeError as error:
            raise SnapshotCorruptError("archive PAX key must be valid UTF-8") from error
        if key == "size" or key.startswith("GNU.sparse"):
            raise SnapshotCorruptError(
                f"archive PAX metadata changes unsupported payload semantics: {key}"
            )
        if key == "path":
            value_start = equals + 1
            value_end = record_end - 1
            _precheck_encoded_member_path(
                payload,
                value_start,
                value_end,
                limits,
            )
            try:
                value = payload[value_start:value_end].decode("utf-8")
            except UnicodeDecodeError as error:
                raise SnapshotCorruptError("archive PAX path must be valid UTF-8") from error
            _canonical_member_name(value, True)
            path = value
        cursor = record_end
    return path, record_count


def _precheck_encoded_member_path(
    payload: bytes,
    start: int,
    end: int,
    limits: WorkspaceLimits,
) -> None:
    if end - start == 1 and payload[start] == ord("."):
        return
    if end - start == 2 and payload[start : start + 2] == b"./":
        return
    if end - start >= 2 and payload[start : start + 2] == b"./":
        start += 2
    if (
        end > start
        and payload[end - 1] == ord("/")
        and (end - start < 2 or payload[end - 2] != ord("/"))
    ):
        end -= 1

    root_prefix_bytes = len(SandboxPath.ROOT.encode("utf-8")) + 1
    if root_prefix_bytes + (end - start) > limits.max_path_bytes:
        raise SnapshotTooLargeError(
            f"archive member path exceeds {limits.max_path_bytes} UTF-8 bytes"
        )

    segment_bytes = 0
    for index in range(start, end):
        if payload[index] == ord("/"):
            if segment_bytes > limits.max_segment_bytes:
                raise SnapshotTooLargeError(
                    f"archive member path segment exceeds {limits.max_segment_bytes} UTF-8 bytes"
                )
            segment_bytes = 0
        else:
            segment_bytes += 1
    if segment_bytes > limits.max_segment_bytes:
        raise SnapshotTooLargeError(
            f"archive member path segment exceeds {limits.max_segment_bytes} UTF-8 bytes"
        )


def _header_block(encoded: bytes, offset: int) -> bytes:
    header_end = offset + tarfile.BLOCKSIZE
    if offset < 0 or header_end > len(encoded):
        raise SnapshotCorruptError("archive member header is truncated")
    return encoded[offset:header_end]


def _decode_header_path_field(field: bytes) -> str:
    terminator = field.find(b"\x00")
    if terminator >= 0:
        if any(field[terminator + 1 :]):
            raise SnapshotCorruptError("archive member path contains embedded NUL data")
        field = field[:terminator]
    try:
        return field.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SnapshotCorruptError("archive member path must be valid UTF-8") from error


def _padded_block_bytes(size: int) -> int:
    return ((size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE


def _member_path(
    raw_name: str,
    is_directory: bool,
    limits: WorkspaceLimits,
) -> SandboxPath | None:
    canonical_name = _canonical_member_name(raw_name, is_directory)
    if canonical_name is None:
        return None

    parts = canonical_name.split("/")
    try:
        encoded_parts = tuple(part.encode("utf-8") for part in parts)
        absolute = f"{SandboxPath.ROOT}/{canonical_name}"
        encoded_absolute = absolute.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SnapshotCorruptError("archive member path must be valid UTF-8") from error

    if any(len(part) > limits.max_segment_bytes for part in encoded_parts):
        raise SnapshotTooLargeError(
            f"archive member path segment exceeds {limits.max_segment_bytes} UTF-8 bytes"
        )
    if len(encoded_absolute) > limits.max_path_bytes:
        raise SnapshotTooLargeError(
            f"archive member path exceeds {limits.max_path_bytes} UTF-8 bytes"
        )
    try:
        return SandboxPath(absolute)
    except InvalidPathError as error:
        raise SnapshotCorruptError("archive member contains an invalid path") from error


def _validate_raw_header_name(
    raw_name: str,
    is_directory: bool,
    *,
    allow_truncated_file_separator: bool,
) -> None:
    if (
        allow_truncated_file_separator
        and not is_directory
        and raw_name.endswith("/")
        and not raw_name.endswith("//")
    ):
        raw_name = raw_name[:-1]
    _canonical_member_name(raw_name, is_directory)


def _canonical_member_name(raw_name: str, is_directory: bool) -> str | None:
    if raw_name in (".", "./"):
        if not is_directory:
            raise SnapshotCorruptError("archive root marker must be a directory")
        return None
    if raw_name.startswith("./"):
        raw_name = raw_name[2:]

    if not raw_name or "\x00" in raw_name:
        raise SnapshotCorruptError("archive member path must not be empty or contain NUL")
    if raw_name.startswith("/") or PurePosixPath(raw_name).is_absolute():
        raise SnapshotCorruptError("archive member path must be relative")
    if "\\" in raw_name or PureWindowsPath(raw_name).drive:
        raise SnapshotCorruptError("archive member path must use portable POSIX syntax")
    if "//" in raw_name:
        raise SnapshotCorruptError("archive member path must be canonical")
    if raw_name.endswith("/"):
        if not is_directory:
            raise SnapshotCorruptError("archive file path must not end with a separator")
        raw_name = raw_name[:-1]

    parts = raw_name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SnapshotCorruptError("archive member path contains traversal or aliases")
    if any(PureWindowsPath(part).drive for part in parts):
        raise SnapshotCorruptError("archive member path contains Windows drive syntax")
    return raw_name


def _reject_sparse_member(member: tarfile.TarInfo) -> None:
    if member.sparse is not None or any(key.startswith("GNU.sparse") for key in member.pax_headers):
        raise SnapshotCorruptError(f"archive sparse member is unsupported: {member.name}")


def _ensure_parent_directories(
    path: SandboxPath,
    entries: dict[SandboxPath, WorkspaceSnapshotEntry],
    synthetic_directories: set[SandboxPath],
    limits: WorkspaceLimits,
    retained_path_bytes: int,
) -> int:
    parent = SandboxPath.root()
    for segment in path.parts[:-1]:
        parent = parent.join(
            segment,
            max_path_bytes=limits.max_path_bytes,
            max_segment_bytes=limits.max_segment_bytes,
        )
        existing = entries.get(parent)
        if existing is not None:
            if existing.kind is not NodeKind.DIRECTORY:
                raise SnapshotCorruptError(f"archive path descends through a file: {parent}")
            continue
        retained_path_bytes = _reserve_path_metadata(
            parent,
            retained_path_bytes,
            limits,
        )
        entries[parent] = WorkspaceSnapshotEntry(
            path=parent,
            kind=NodeKind.DIRECTORY,
        )
        synthetic_directories.add(parent)
        _check_node_limit(entries, limits)
    return retained_path_bytes


def _reserve_path_metadata(
    path: SandboxPath,
    retained_path_bytes: int,
    limits: WorkspaceLimits,
) -> int:
    retained_path_bytes += len(path.value.encode("utf-8"))
    if retained_path_bytes > limits.max_snapshot_bytes:
        raise SnapshotTooLargeError(
            f"archive canonical paths retain more than {limits.max_snapshot_bytes} UTF-8 bytes"
        )
    return retained_path_bytes


def _check_node_limit(
    entries: dict[SandboxPath, WorkspaceSnapshotEntry],
    limits: WorkspaceLimits,
) -> None:
    if len(entries) + 1 > limits.max_nodes:
        raise SnapshotTooLargeError(f"archive tree contains more than {limits.max_nodes} nodes")


def _validate_tree_limits(
    entries: tuple[WorkspaceSnapshotEntry, ...],
    total_bytes: int,
    limits: WorkspaceLimits,
) -> None:
    if len(entries) + 1 > limits.max_nodes:
        raise SnapshotTooLargeError(f"archive tree contains more than {limits.max_nodes} nodes")
    if total_bytes > limits.max_total_bytes:
        raise SnapshotTooLargeError(
            f"archive files contain more than {limits.max_total_bytes} bytes"
        )
    for entry in entries:
        portable_path = _member_path(
            "/".join(entry.path.parts),
            entry.kind is NodeKind.DIRECTORY,
            limits,
        )
        if portable_path != entry.path:
            raise SnapshotCorruptError(f"workspace entry path is not portable: {entry.path}")
        _validate_entry_path_limits(entry.path, limits)
        if (
            entry.kind is NodeKind.FILE
            and entry.content is not None
            and len(entry.content) > limits.max_file_bytes
        ):
            raise SnapshotTooLargeError(
                f"archive file {entry.path} contains {len(entry.content)} bytes; limit is "
                f"{limits.max_file_bytes} bytes"
            )


def _validate_entry_path_limits(path: SandboxPath, limits: WorkspaceLimits) -> None:
    try:
        path_bytes = path.value.encode("utf-8")
        part_bytes = tuple(part.encode("utf-8") for part in path.parts)
    except UnicodeEncodeError as error:
        raise SnapshotCorruptError("workspace entry path must be valid UTF-8") from error
    if len(path_bytes) > limits.max_path_bytes:
        raise SnapshotTooLargeError(
            f"archive member path exceeds {limits.max_path_bytes} UTF-8 bytes"
        )
    if any(len(part) > limits.max_segment_bytes for part in part_bytes):
        raise SnapshotTooLargeError(
            f"archive member path segment exceeds {limits.max_segment_bytes} UTF-8 bytes"
        )
