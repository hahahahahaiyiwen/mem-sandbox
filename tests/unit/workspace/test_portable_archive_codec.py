from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import sys
import tarfile
import tracemalloc
from dataclasses import dataclass
from typing import cast

import pytest

from mem_sandbox.workspace import (
    ContentHash,
    NodeKind,
    PortableWorkspaceArchiveCodec,
    SandboxPath,
    SnapshotCorruptError,
    SnapshotIncompatibleError,
    SnapshotTooLargeError,
    WorkspaceLimits,
    WorkspaceSnapshotEntry,
)
from mem_sandbox.workspace.hashing import hash_directory

_LONG_FILE_NAME = f"{'x' * 101}.bin"
_DEEP_DIRECTORY_COUNT = 1_100
_CANONICAL_ARCHIVE_SHA256 = "3c6ddabef4057ea3b69319a4b2edff5bf0de6ee81022c6af2a6be019acae06dc"


@dataclass(frozen=True, slots=True)
class _TarMemberSpec:
    name: str
    content: bytes
    member_type: bytes
    mode: int
    pax_headers: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class _ExpectedDirectory:
    children: dict[str, _ExpectedNode]


type _ExpectedNode = _ExpectedDirectory | bytes


def _file(name: str, content: bytes) -> _TarMemberSpec:
    return _TarMemberSpec(name=name, content=content, member_type=tarfile.REGTYPE, mode=0o644)


def _directory(name: str) -> _TarMemberSpec:
    return _TarMemberSpec(name=name, content=b"", member_type=tarfile.DIRTYPE, mode=0o755)


def _special(name: str, member_type: bytes) -> _TarMemberSpec:
    return _TarMemberSpec(name=name, content=b"", member_type=member_type, mode=0o644)


def _pax_sparse(name: str) -> _TarMemberSpec:
    return _TarMemberSpec(
        name=name,
        content=b"",
        member_type=tarfile.REGTYPE,
        mode=0o644,
        pax_headers=(
            ("GNU.sparse.size", "1"),
            ("GNU.sparse.numblocks", "0"),
        ),
    )


def test_archive_export_is_byte_canonical_posix_relative_and_pax_compatible() -> None:
    codec = PortableWorkspaceArchiveCodec()
    entries = _canonical_entries()

    first = codec.encode(entries, WorkspaceLimits())
    second = codec.encode(tuple(reversed(entries)), WorkspaceLimits())

    assert first == second
    assert hashlib.sha256(first).hexdigest() == _CANONICAL_ARCHIVE_SHA256
    members = _read_members(first)
    expected_modes = {
        "empty": 0o755,
        "long": 0o755,
        f"long/{_LONG_FILE_NAME}": 0o644,
        "src": 0o755,
        "src/app.py": 0o644,
        "unicode": 0o755,
        "unicode/é中.bin": 0o644,
    }
    assert [member.name for member in members] == list(expected_modes)
    for member in members:
        assert not member.name.startswith("/")
        assert "\\" not in member.name
        assert ".." not in member.name.split("/")
        assert member.uid == 0
        assert member.gid == 0
        assert member.uname == ""
        assert member.gname == ""
        assert member.mtime == 0
        assert member.mode == expected_modes[member.name]
    assert members[2].pax_headers == {"path": f"long/{_LONG_FILE_NAME}"}
    assert members[6].pax_headers == {"path": "unicode/é中.bin"}


@pytest.mark.parametrize("kind", ("file", object()))
def test_snapshot_entry_rejects_runtime_invalid_node_kinds(kind: object) -> None:
    with pytest.raises(TypeError):
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/lost.bin"),
            kind=cast(NodeKind, kind),
            content=b"payload",
        )


def test_archive_import_preserves_binary_files_empty_directories_and_implicit_parents() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes(
        (
            _directory("."),
            _directory("empty"),
            _file("./bin/data.bin", b"\x00\xffbinary\n"),
        )
    )

    decoded = codec.decode(archive, WorkspaceLimits())

    assert decoded.entries == (
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/bin"),
            kind=NodeKind.DIRECTORY,
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/bin/data.bin"),
            kind=NodeKind.FILE,
            content=b"\x00\xffbinary\n",
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/empty"),
            kind=NodeKind.DIRECTORY,
        ),
    )
    assert decoded.tree_stats.total_bytes == len(b"\x00\xffbinary\n")
    assert decoded.tree_stats.node_count == 4
    assert decoded.tree_stats.root_hash == _root_hash_for(decoded.entries)


def test_archive_import_counts_one_optional_root_marker_without_counting_it_as_state() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes(
        (
            _directory("."),
            _file("file", b""),
        )
    )

    decoded = codec.decode(
        archive,
        WorkspaceLimits(
            max_file_bytes=1,
            max_total_bytes=1,
            max_nodes=2,
        ),
    )

    assert decoded.tree_stats.node_count == 2


def test_archive_import_rejects_duplicate_or_nonempty_root_markers() -> None:
    codec = PortableWorkspaceArchiveCodec()

    with pytest.raises(SnapshotCorruptError):
        codec.decode(
            _tar_bytes((_directory("."), _directory("./"))),
            WorkspaceLimits(),
        )
    with pytest.raises(SnapshotCorruptError):
        codec.decode(
            _tar_bytes(
                (
                    _TarMemberSpec(
                        name=".",
                        content=b"x",
                        member_type=tarfile.DIRTYPE,
                        mode=0o755,
                    ),
                )
            ),
            WorkspaceLimits(),
        )


@pytest.mark.parametrize(
    "member",
    (
        _directory("alias//"),
        _TarMemberSpec(
            name="placeholder",
            content=b"",
            member_type=tarfile.DIRTYPE,
            mode=0o755,
            pax_headers=(("path", "alias//"),),
        ),
    ),
)
def test_archive_import_rejects_exact_directory_path_aliases(
    member: _TarMemberSpec,
) -> None:
    codec = PortableWorkspaceArchiveCodec()

    with pytest.raises(SnapshotCorruptError):
        codec.decode(_tar_bytes((member,)), WorkspaceLimits())


@pytest.mark.parametrize(
    "name",
    (
        "/absolute.txt",
        "../escape.txt",
        "safe/../../escape.txt",
        "C:/host/path.txt",
        "dir/C:/file.txt",
        "dir/C:",
        "segment\\windows.txt",
        "nested//file.txt",
        "nested/./file.txt",
        "nul\x00path.txt",
        ".",
    ),
)
def test_archive_import_rejects_non_portable_or_noncanonical_file_paths(name: str) -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes((_file(name, b"content"),))

    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive, WorkspaceLimits())


@pytest.mark.parametrize(
    "members",
    (
        (_file("dup.txt", b"first"), _file("./dup.txt", b"second")),
        (_directory("dup"), _directory("./dup")),
        (_file("conflict", b"file"), _directory("conflict")),
        (_file("parent", b"file"), _file("parent/child.txt", b"child")),
    ),
)
def test_archive_import_rejects_normalized_duplicates_and_type_conflicts(
    members: tuple[_TarMemberSpec, ...],
) -> None:
    codec = PortableWorkspaceArchiveCodec()

    with pytest.raises(SnapshotCorruptError):
        codec.decode(_tar_bytes(members), WorkspaceLimits())


def test_archive_import_reports_duplicate_before_total_byte_limit() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes(
        (
            _file("dup", b"one"),
            _file("dup", b"two"),
        )
    )

    with pytest.raises(SnapshotCorruptError, match="duplicated"):
        codec.decode(
            archive,
            WorkspaceLimits(
                max_file_bytes=3,
                max_total_bytes=4,
            ),
        )


def test_archive_import_rejects_leaf_node_overflow_before_reading_payload() -> None:
    codec = PortableWorkspaceArchiveCodec()
    content = b"x" * (1024 * 1024)
    archive = _tar_bytes((_file("parent/file", content),))

    tracemalloc.start()
    try:
        with pytest.raises(SnapshotTooLargeError):
            codec.decode(
                archive,
                WorkspaceLimits(
                    max_file_bytes=len(content),
                    max_total_bytes=len(content),
                    max_nodes=2,
                ),
            )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak_bytes < 256 * 1024


@pytest.mark.parametrize(
    "member_type",
    (
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.FIFOTYPE,
        tarfile.CONTTYPE,
        tarfile.GNUTYPE_SPARSE,
    ),
)
def test_archive_import_rejects_links_devices_sparse_and_other_unsupported_members(
    member_type: bytes,
) -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes((_special("unsupported", member_type),))

    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive, WorkspaceLimits())


def test_archive_import_rejects_pax_sparse_metadata() -> None:
    codec = PortableWorkspaceArchiveCodec()

    with pytest.raises(SnapshotCorruptError):
        codec.decode(_tar_bytes((_pax_sparse("unsupported"),)), WorkspaceLimits())


def test_archive_import_rejects_pax_payload_size_overrides() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes(
        (
            _TarMemberSpec(
                name="unsupported",
                content=b"",
                member_type=tarfile.REGTYPE,
                mode=0o644,
                pax_headers=(("size", "1024"),),
            ),
        )
    )

    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive, WorkspaceLimits())


@pytest.mark.parametrize(
    "name",
    (
        "a" * 101,
        f"{'a' * 101}\x00hidden",
    ),
)
def test_archive_import_rejects_gnu_long_name_extensions(name: str) -> None:
    codec = PortableWorkspaceArchiveCodec()

    with pytest.raises(SnapshotCorruptError):
        codec.decode(_gnu_tar_bytes(name, b"content"), WorkspaceLimits())


@pytest.mark.parametrize(
    "raw_name",
    (
        b"/absolute",
        b"safe//alias",
        b"safe\x00../hidden",
    ),
)
def test_pax_path_does_not_mask_an_unsafe_raw_header_name(raw_name: bytes) -> None:
    codec = PortableWorkspaceArchiveCodec()
    safe_pax_name = "s" * 101
    archive = _rewrite_member_header_name(
        _tar_bytes((_file(safe_pax_name, b"content"),)),
        raw_name,
    )

    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive, WorkspaceLimits())


def test_pax_path_does_not_mask_an_unsafe_gnu_prefix() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _rewrite_member_prefix(
        _pax_header((("path", "safe.txt"),)) + _tar_bytes((_file("fallback", b"content"),)),
        b"../escape",
        tarfile.GNU_MAGIC,
    )

    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive, WorkspaceLimits())


def test_archive_import_rejects_stacked_local_pax_headers_without_recursion() -> None:
    codec = PortableWorkspaceArchiveCodec()
    base = _tar_bytes((_file("safe.txt", b""),))
    nested = _pax_header((("comment", "outer"),)) + _pax_header((("path", "../escape"),)) + base
    deeply_nested = (
        b"".join(_pax_header((("comment", str(index)),)) for index in range(1_100)) + base
    )

    with pytest.raises(SnapshotCorruptError):
        codec.decode(nested, WorkspaceLimits())
    with pytest.raises(SnapshotCorruptError):
        codec.decode(deeply_nested, WorkspaceLimits())


def test_archive_import_bounds_total_pax_record_count() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _pax_header(tuple(("x", str(index)) for index in range(101))) + _tar_bytes(
        (_file("safe.txt", b""),)
    )

    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            archive,
            WorkspaceLimits(max_nodes=100),
        )


def test_archive_import_rejects_oversized_pax_path_before_component_amplification() -> None:
    codec = PortableWorkspaceArchiveCodec()
    oversized_path = ("a/" * 500_000) + "file"
    archive = _tar_bytes(
        (
            _TarMemberSpec(
                name="placeholder",
                content=b"",
                member_type=tarfile.REGTYPE,
                mode=0o644,
                pax_headers=(("path", oversized_path),),
            ),
        )
    )

    tracemalloc.start()
    try:
        with pytest.raises(SnapshotTooLargeError):
            codec.decode(
                archive,
                WorkspaceLimits(max_snapshot_bytes=len(archive)),
            )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak_bytes < 6 * 1024 * 1024


def test_archive_import_rejects_non_posix_prefix_semantics() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _rewrite_member_prefix(
        _tar_bytes((_file("safe.txt", b"content"),)),
        b"../escape",
        b"\x00" * 8,
    )

    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive, WorkspaceLimits())


@pytest.mark.parametrize("root_name", (".", "./"))
def test_archive_import_accepts_pax_root_markers_at_exact_root_path_limit(
    root_name: str,
) -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes(
        (
            _TarMemberSpec(
                name="placeholder",
                content=b"",
                member_type=tarfile.DIRTYPE,
                mode=0o755,
                pax_headers=(("path", root_name),),
            ),
        )
    )

    decoded = codec.decode(
        archive,
        WorkspaceLimits(
            max_path_bytes=len(SandboxPath.ROOT.encode("utf-8")),
            max_segment_bytes=len(b"workspace"),
        ),
    )

    assert decoded.entries == ()


def test_archive_import_accepts_gzip_and_rejects_other_compression_profiles() -> None:
    codec = PortableWorkspaceArchiveCodec()
    plain = _tar_bytes((_file("file.bin", b"\x00\xff"),))

    decoded = codec.decode(gzip.compress(plain, mtime=0), WorkspaceLimits())

    assert decoded.entries[0].content == b"\x00\xff"
    with pytest.raises(SnapshotIncompatibleError):
        codec.decode(bz2.compress(plain), WorkspaceLimits())


@pytest.mark.parametrize("name", ("BZh-file", "PK\x03\x04-file"))
def test_uncompressed_tar_header_takes_precedence_over_compression_magic(name: str) -> None:
    codec = PortableWorkspaceArchiveCodec()

    decoded = codec.decode(
        _tar_bytes((_file(name, b"content"),)),
        WorkspaceLimits(),
    )

    assert decoded.entries[0].path.name == name


def test_archive_import_rejects_malformed_input() -> None:
    codec = PortableWorkspaceArchiveCodec()

    with pytest.raises(SnapshotCorruptError):
        codec.decode(b"not a tar archive", WorkspaceLimits())


def test_archive_import_requires_two_zero_end_blocks_and_zero_only_trailing_padding() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes((_file("file", b"x"),))
    member_end = 2 * tarfile.BLOCKSIZE
    one_end_block = archive[: member_end + tarfile.BLOCKSIZE]
    two_end_blocks = archive[: member_end + (2 * tarfile.BLOCKSIZE)]

    assert codec.decode(two_end_blocks, WorkspaceLimits()).entries[0].content == b"x"
    with pytest.raises(SnapshotCorruptError):
        codec.decode(archive[:member_end], WorkspaceLimits())
    with pytest.raises(SnapshotCorruptError):
        codec.decode(one_end_block, WorkspaceLimits())
    with pytest.raises(SnapshotCorruptError):
        codec.decode(two_end_blocks + (b"x" * tarfile.BLOCKSIZE), WorkspaceLimits())


def test_archive_import_requires_zero_filled_member_padding() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = bytearray(_tar_bytes((_file("file", b"x"),)))
    archive[tarfile.BLOCKSIZE + 1] = ord("x")

    with pytest.raises(SnapshotCorruptError, match="padding"):
        codec.decode(bytes(archive), WorkspaceLimits())


def test_archive_import_supports_a_tree_deeper_than_the_python_recursion_limit() -> None:
    assert _DEEP_DIRECTORY_COUNT > sys.getrecursionlimit()
    codec = PortableWorkspaceArchiveCodec()
    deep_name = "/".join((*("a",) * _DEEP_DIRECTORY_COUNT, "file"))

    decoded = codec.decode(
        _tar_bytes((_file(deep_name, b"content"),)),
        WorkspaceLimits(),
    )

    assert decoded.tree_stats.node_count == _DEEP_DIRECTORY_COUNT + 2
    assert decoded.entries[-1].content == b"content"


def test_archive_import_honors_configured_path_limits_above_defaults() -> None:
    codec = PortableWorkspaceArchiveCodec()
    long_segment = "x" * 300
    relative_path = "/".join((*((long_segment,) * 14), "file"))
    absolute_path = f"{SandboxPath.ROOT}/{relative_path}"
    archive = _tar_bytes((_file(relative_path, b"content"),))
    limits = WorkspaceLimits(
        max_path_bytes=len(absolute_path.encode("utf-8")),
        max_segment_bytes=len(long_segment.encode("utf-8")),
    )

    decoded = codec.decode(archive, limits)

    assert decoded.entries[-1].path == SandboxPath(absolute_path)
    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            archive,
            WorkspaceLimits(
                max_path_bytes=limits.max_path_bytes - 1,
                max_segment_bytes=limits.max_segment_bytes,
            ),
        )


def test_archive_import_bounds_cumulative_synthesized_path_metadata() -> None:
    codec = PortableWorkspaceArchiveCodec()
    segment = "x" * 20
    relative_path = "/".join((*((segment,) * 800), "file"))
    absolute_path = f"{SandboxPath.ROOT}/{relative_path}"
    archive = _tar_bytes((_file(relative_path, b""),))
    assert len(archive) < 64 * 1024

    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            archive,
            WorkspaceLimits(
                max_nodes=1_000,
                max_path_bytes=len(absolute_path.encode("utf-8")),
                max_snapshot_bytes=64 * 1024,
            ),
        )


def test_archive_import_accepts_exact_file_node_path_segment_and_input_limits() -> None:
    codec = PortableWorkspaceArchiveCodec()
    archive = _tar_bytes((_file("abcdefghij", b"1234"),))
    limits = WorkspaceLimits(
        max_file_bytes=4,
        max_total_bytes=4,
        max_nodes=2,
        max_path_bytes=len(b"/workspace/abcdefghij"),
        max_segment_bytes=len(b"abcdefghij"),
        max_snapshot_bytes=len(archive),
    )

    decoded = codec.decode(archive, limits)

    assert decoded.tree_stats.total_bytes == 4
    assert decoded.tree_stats.node_count == 2


def test_archive_import_rejects_each_limit_one_over_without_conflating_limits() -> None:
    codec = PortableWorkspaceArchiveCodec()
    one_file = _tar_bytes((_file("abcdefghij", b"1234"),))
    two_files = _tar_bytes((_file("a", b"12"), _file("b", b"34")))

    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            one_file,
            WorkspaceLimits(max_file_bytes=3, max_total_bytes=4),
        )
    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            two_files,
            WorkspaceLimits(max_file_bytes=2, max_total_bytes=3),
        )
    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            two_files,
            WorkspaceLimits(max_file_bytes=2, max_total_bytes=4, max_nodes=2),
        )
    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            one_file,
            WorkspaceLimits(
                max_file_bytes=4,
                max_total_bytes=4,
                max_path_bytes=len(b"/workspace/abcdefghij") - 1,
                max_segment_bytes=len(b"abcdefghij"),
            ),
        )
    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            one_file,
            WorkspaceLimits(
                max_file_bytes=4,
                max_total_bytes=4,
                max_path_bytes=100,
                max_segment_bytes=len(b"abcdefghij") - 1,
            ),
        )
    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            one_file,
            WorkspaceLimits(
                max_file_bytes=4,
                max_total_bytes=4,
                max_snapshot_bytes=len(one_file) - 1,
            ),
        )


def test_gzip_expansion_is_bounded_separately_from_compressed_input() -> None:
    codec = PortableWorkspaceArchiveCodec()
    plain = _tar_bytes((_file("file", b""),))
    compressed = gzip.compress(plain, mtime=0)
    assert len(compressed) < 1_024 < len(plain)

    with pytest.raises(SnapshotTooLargeError):
        codec.decode(
            compressed,
            WorkspaceLimits(
                max_file_bytes=1,
                max_total_bytes=1,
                max_snapshot_bytes=1_024,
            ),
        )


def test_archive_export_enforces_encoded_output_limit() -> None:
    codec = PortableWorkspaceArchiveCodec()
    entry = WorkspaceSnapshotEntry(
        path=SandboxPath.resolve("/workspace/file"),
        kind=NodeKind.FILE,
        content=b"",
    )

    with pytest.raises(SnapshotTooLargeError):
        codec.encode(
            (entry,),
            WorkspaceLimits(
                max_file_bytes=1,
                max_total_bytes=1,
                max_snapshot_bytes=1_024,
            ),
        )


def test_archive_export_rejects_before_buffering_an_oversized_file() -> None:
    codec = PortableWorkspaceArchiveCodec()
    content = b"x" * (1024 * 1024)
    entry = WorkspaceSnapshotEntry(
        path=SandboxPath.resolve("/workspace/file"),
        kind=NodeKind.FILE,
        content=content,
    )
    limits = WorkspaceLimits(
        max_file_bytes=len(content),
        max_total_bytes=len(content),
        max_snapshot_bytes=1_024,
    )

    tracemalloc.start()
    try:
        with pytest.raises(SnapshotTooLargeError):
            codec.encode((entry,), limits)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak_bytes < 256 * 1024


def test_archive_codec_never_uses_host_filesystem_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec = PortableWorkspaceArchiveCodec()
    entries = _canonical_entries()
    archive = _tar_bytes((_file("file", b"content"),))

    def fail_host_access(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"unexpected host filesystem access: {args!r} {kwargs!r}")

    monkeypatch.setattr("builtins.open", fail_host_access)
    monkeypatch.setattr("tempfile.TemporaryFile", fail_host_access)
    monkeypatch.setattr(tarfile.TarFile, "extract", fail_host_access)
    monkeypatch.setattr(tarfile.TarFile, "extractall", fail_host_access)

    assert codec.encode(entries, WorkspaceLimits())
    assert codec.decode(archive, WorkspaceLimits()).entries


def _canonical_entries() -> tuple[WorkspaceSnapshotEntry, ...]:
    return (
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/src"),
            kind=NodeKind.DIRECTORY,
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/src/app.py"),
            kind=NodeKind.FILE,
            content=b"print('hello')\n",
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/empty"),
            kind=NodeKind.DIRECTORY,
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/long"),
            kind=NodeKind.DIRECTORY,
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve(f"/workspace/long/{_LONG_FILE_NAME}"),
            kind=NodeKind.FILE,
            content=b"\x00\xff",
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/unicode"),
            kind=NodeKind.DIRECTORY,
        ),
        WorkspaceSnapshotEntry(
            path=SandboxPath.resolve("/workspace/unicode/é中.bin"),
            kind=NodeKind.FILE,
            content=b"utf8",
        ),
    )


def _tar_bytes(members: tuple[_TarMemberSpec, ...]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer,
        mode="w",
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="strict",
    ) as archive:
        for spec in members:
            info = tarfile.TarInfo(spec.name)
            info.type = spec.member_type
            info.mode = spec.mode
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.size = len(spec.content)
            info.pax_headers = dict(spec.pax_headers)
            archive.addfile(info, io.BytesIO(spec.content) if info.size else None)
    return buffer.getvalue()


def _gnu_tar_bytes(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer,
        mode="w",
        format=tarfile.GNU_FORMAT,
        encoding="utf-8",
        errors="strict",
    ) as archive:
        info = tarfile.TarInfo(name)
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _rewrite_member_header_name(encoded: bytes, raw_name: bytes) -> bytes:
    assert len(raw_name) <= 100
    with tarfile.open(
        fileobj=io.BytesIO(encoded),
        mode="r:",
        encoding="utf-8",
        errors="strict",
    ) as archive:
        member = next(iter(archive))
        header_start = member.offset_data - tarfile.BLOCKSIZE

    rewritten = bytearray(encoded)
    header = bytearray(rewritten[header_start : header_start + tarfile.BLOCKSIZE])
    header[:100] = b"\x00" * 100
    header[: len(raw_name)] = raw_name
    header[345:500] = b"\x00" * 155
    header[148:156] = b" " * 8
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}\x00 ".encode("ascii")
    rewritten[header_start : header_start + tarfile.BLOCKSIZE] = header
    return bytes(rewritten)


def _rewrite_member_prefix(
    encoded: bytes,
    prefix: bytes,
    magic: bytes,
) -> bytes:
    assert len(prefix) <= 155
    assert len(magic) == 8
    with tarfile.open(
        fileobj=io.BytesIO(encoded),
        mode="r:",
        encoding="utf-8",
        errors="strict",
    ) as archive:
        member = next(iter(archive))
        header_start = member.offset_data - tarfile.BLOCKSIZE

    rewritten = bytearray(encoded)
    header = bytearray(rewritten[header_start : header_start + tarfile.BLOCKSIZE])
    header[257:265] = magic
    header[345:500] = b"\x00" * 155
    header[345 : 345 + len(prefix)] = prefix
    header[148:156] = b" " * 8
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}\x00 ".encode("ascii")
    rewritten[header_start : header_start + tarfile.BLOCKSIZE] = header
    return bytes(rewritten)


def _pax_header(fields: tuple[tuple[str, str], ...]) -> bytes:
    payload = b"".join(_pax_record(key, value) for key, value in fields)
    info = tarfile.TarInfo("././@PaxHeader")
    info.type = tarfile.XHDTYPE
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.size = len(payload)
    header = info.tobuf(
        format=tarfile.USTAR_FORMAT,
        encoding="utf-8",
        errors="strict",
    )
    padding = (-len(payload)) % tarfile.BLOCKSIZE
    return header + payload + (b"\x00" * padding)


def _pax_record(key: str, value: str) -> bytes:
    body = f" {key}={value}\n".encode()
    length = len(body) + 1
    while True:
        record = str(length).encode() + body
        if len(record) == length:
            return record
        length = len(record)


def _read_members(archive: bytes) -> tuple[tarfile.TarInfo, ...]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as reader:
        return tuple(reader.getmembers())


def _root_hash_for(entries: tuple[WorkspaceSnapshotEntry, ...]) -> ContentHash:
    tree = _ExpectedDirectory(children={})
    for entry in sorted(entries, key=lambda item: item.path.value):
        parent = tree
        for segment in entry.path.parts[:-1]:
            child = parent.children.get(segment)
            if child is None:
                child = _ExpectedDirectory(children={})
                parent.children[segment] = child
            assert isinstance(child, _ExpectedDirectory)
            parent = child
        if entry.kind is NodeKind.DIRECTORY:
            parent.children[entry.path.name] = _ExpectedDirectory(children={})
        else:
            assert entry.content is not None
            parent.children[entry.path.name] = entry.content
    return _hash_tree(tree)


def _hash_tree(node: _ExpectedNode) -> ContentHash:
    if isinstance(node, bytes):
        return ContentHash.from_bytes(node)
    return hash_directory(
        (
            name,
            NodeKind.FILE if isinstance(child, bytes) else NodeKind.DIRECTORY,
            _hash_tree(child),
        )
        for name, child in node.children.items()
    )
