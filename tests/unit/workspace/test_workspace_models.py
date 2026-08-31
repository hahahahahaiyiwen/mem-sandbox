from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    PathMustNotExist,
    SandboxPath,
    WorkspaceLimits,
    WorkspaceWriteRequest,
)


def test_content_hash_is_lowercase_sha256() -> None:
    content_hash = ContentHash.from_bytes(b"hello")

    assert content_hash.value == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert str(content_hash) == content_hash.value


@pytest.mark.parametrize(
    "value",
    (
        "",
        "abc",
        "G" * 64,
        "a" * 63,
        "a" * 65,
    ),
)
def test_content_hash_rejects_non_sha256_text(value: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ContentHash(value)


def test_workspace_limit_defaults_are_conservative_and_configurable() -> None:
    limits = WorkspaceLimits()

    assert limits.max_file_bytes == 4 * 1024 * 1024
    assert limits.max_total_bytes == 16 * 1024 * 1024
    assert limits.max_nodes == 10_000
    assert limits.max_path_bytes == 4_096
    assert limits.max_segment_bytes == 255
    assert limits.max_read_bytes == 256 * 1024
    assert limits.max_read_lines == 2_000
    assert limits.max_patch_bytes == 1024 * 1024
    assert limits.max_snapshot_bytes == 32 * 1024 * 1024

    assert WorkspaceLimits(max_file_bytes=10).max_file_bytes == 10


@pytest.mark.parametrize(
    "field",
    (
        "max_file_bytes",
        "max_total_bytes",
        "max_nodes",
        "max_path_bytes",
        "max_segment_bytes",
        "max_read_bytes",
        "max_read_lines",
        "max_patch_bytes",
        "max_snapshot_bytes",
    ),
)
def test_workspace_limits_require_positive_integers(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        WorkspaceLimits(**{field: 0})

    with pytest.raises(TypeError, match=field):
        WorkspaceLimits(**{field: True})


def test_workspace_limits_reject_impossible_relationships() -> None:
    with pytest.raises(ValueError, match="max_file_bytes"):
        WorkspaceLimits(max_file_bytes=11, max_total_bytes=10)

    with pytest.raises(ValueError, match="max_path_bytes"):
        WorkspaceLimits(max_path_bytes=10, max_segment_bytes=11)

    with pytest.raises(ValueError, match="workspace root"):
        WorkspaceLimits(max_path_bytes=9, max_segment_bytes=9)

    with pytest.raises(ValueError, match="workspace root"):
        WorkspaceLimits(max_path_bytes=10, max_segment_bytes=8)


def test_write_preconditions_are_explicit_and_immutable() -> None:
    expected_hash = ContentHash.from_bytes(b"before")
    request = WorkspaceWriteRequest(
        path=SandboxPath.resolve("/workspace/file.txt"),
        content=b"after",
        precondition=ContentHashMustEqual(expected_hash),
    )

    assert isinstance(AnyCurrentState(), AnyCurrentState)
    assert isinstance(PathMustNotExist(), PathMustNotExist)
    assert request.precondition == ContentHashMustEqual(expected_hash)

    with pytest.raises(FrozenInstanceError):
        request.content = b"changed"  # type: ignore[misc]


def test_write_request_requires_bytes() -> None:
    with pytest.raises(TypeError, match="content"):
        WorkspaceWriteRequest(
            path=SandboxPath.root(),
            content="text",  # type: ignore[arg-type]
            precondition=AnyCurrentState(),
        )
