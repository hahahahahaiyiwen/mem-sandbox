from __future__ import annotations

import pytest

from mem_sandbox.workspace import (
    InvalidPathError,
    PathOutsideWorkspaceError,
    SandboxPath,
)


def test_absolute_path_is_normalized_under_workspace_root() -> None:
    path = SandboxPath.resolve("/workspace/src/./mem_sandbox//main.py")

    assert str(path) == "/workspace/src/mem_sandbox/main.py"
    assert path.parts == ("src", "mem_sandbox", "main.py")
    assert path.name == "main.py"
    assert str(path.parent) == "/workspace/src/mem_sandbox"


def test_relative_path_requires_and_uses_explicit_cwd() -> None:
    cwd = SandboxPath.resolve("/workspace/src")

    assert SandboxPath.resolve("package/module.py", cwd=cwd) == SandboxPath.resolve(
        "/workspace/src/package/module.py"
    )

    with pytest.raises(InvalidPathError, match="working directory"):
        SandboxPath.resolve("module.py")


def test_root_and_case_sensitive_comparison_are_stable() -> None:
    root = SandboxPath.root()

    assert root.is_root
    assert root.parts == ()
    assert root.parent == root
    assert SandboxPath.resolve("/workspace/A.py") != SandboxPath.resolve("/workspace/a.py")


def test_backslash_is_a_posix_filename_character_not_a_separator() -> None:
    path = SandboxPath.resolve(r"folder\file.txt", cwd=SandboxPath.root())

    assert path.parts == (r"folder\file.txt",)


@pytest.mark.parametrize(
    "value",
    (
        "",
        "/workspace/../escape",
        "../escape",
        "/workspace/a/../../escape",
        "/workspace/\x00file",
    ),
)
def test_invalid_or_untrusted_paths_are_rejected(value: str) -> None:
    with pytest.raises(InvalidPathError):
        SandboxPath.resolve(value, cwd=SandboxPath.root())


@pytest.mark.parametrize("value", ("/", "/tmp/file", "/workspace-other/file"))
def test_paths_outside_workspace_are_rejected(value: str) -> None:
    with pytest.raises(PathOutsideWorkspaceError):
        SandboxPath.resolve(value)


def test_path_and_segment_limits_count_utf8_bytes() -> None:
    with pytest.raises(InvalidPathError, match="segment"):
        SandboxPath.resolve(
            "/workspace/\N{SNOWMAN}",
            max_segment_bytes=2,
        )

    with pytest.raises(InvalidPathError, match="path"):
        SandboxPath.resolve(
            "/workspace/abc",
            max_path_bytes=len(b"/workspace/abc") - 1,
        )


def test_join_preserves_root_confinement() -> None:
    path = SandboxPath.resolve("/workspace/src").join("mem_sandbox", "workspace")

    assert path == SandboxPath.resolve("/workspace/src/mem_sandbox/workspace")

    with pytest.raises(InvalidPathError):
        path.join("..", "escape")
