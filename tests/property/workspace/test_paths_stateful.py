from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType]
)
from tests.property.strategies import (
    InvalidPathCase,
    Utf8Boundary,
    invalid_path_cases,
    path_segments,
    safe_segments,
    utf8_boundaries,
)

from mem_sandbox.workspace import (
    InvalidPathError,
    PathOutsideWorkspaceError,
    SandboxPath,
)


def _absolute_source(segments: tuple[str, ...], noisy: bool) -> str:
    if not segments:
        return "/workspace//./" if noisy else "/workspace"
    separator = "//" if noisy else "/"
    suffix = separator.join(segments)
    return f"/workspace/{'./' if noisy else ''}{suffix}{'/' if noisy else ''}"


def _relative_source(segments: tuple[str, ...], noisy: bool) -> str:
    separator = "//" if noisy else "/"
    suffix = separator.join(segments)
    return f"./{suffix}/" if noisy else suffix


class SandboxPathStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.cwd = SandboxPath.root()
        self.accepted: set[SandboxPath] = {self.cwd}

    @rule(segments=path_segments(), noisy=st.booleans())
    def resolve_absolute(self, segments: tuple[str, ...], noisy: bool) -> None:
        resolved = SandboxPath.resolve(_absolute_source(segments, noisy))
        assert resolved.parts == segments
        self.accepted.add(resolved)

    @rule(segments=path_segments(min_size=1), noisy=st.booleans())
    def resolve_relative(self, segments: tuple[str, ...], noisy: bool) -> None:
        resolved = SandboxPath.resolve(_relative_source(segments, noisy), cwd=self.cwd)
        assert resolved.parts == (*self.cwd.parts, *segments)
        self.accepted.add(resolved)

    @rule(segment=safe_segments(max_size=6))
    def join_child(self, segment: str) -> None:
        joined = self.cwd.join(segment)
        assert joined.parts == (*self.cwd.parts, segment)
        assert self.cwd.is_ancestor_of(joined)
        self.accepted.add(joined)

    @rule(segments=path_segments())
    def move_working_directory(self, segments: tuple[str, ...]) -> None:
        self.cwd = SandboxPath.resolve(_absolute_source(segments, False))
        self.accepted.add(self.cwd)

    @rule(case=invalid_path_cases())
    def reject_invalid_category(self, case: InvalidPathCase) -> None:
        expected = (
            PathOutsideWorkspaceError
            if case.category in {"outside-root", "sibling-prefix"}
            else InvalidPathError
        )
        with pytest.raises(expected):
            SandboxPath.resolve(case.value, cwd=self.cwd)

    @invariant()
    def accepted_paths_remain_canonical_and_reconstructable(self) -> None:
        for path in self.accepted:
            assert path.value.startswith(SandboxPath.ROOT)
            assert "//" not in path.value
            assert "/./" not in path.value
            assert path == SandboxPath.resolve(path.value)
            if path.is_root:
                assert path.parent == path
            else:
                assert path.parent.join(path.name) == path


def test_sandbox_path_state_machine() -> None:
    run_state_machine_as_test(SandboxPathStateMachine)


@given(boundary=utf8_boundaries())
def test_segment_utf8_exact_and_one_over_boundaries(boundary: Utf8Boundary) -> None:
    exact = SandboxPath.resolve(
        f"/workspace/{boundary.exact}",
        max_segment_bytes=boundary.limit,
    )

    assert len(exact.name.encode("utf-8")) == boundary.limit
    with pytest.raises(InvalidPathError, match="segment"):
        SandboxPath.resolve(
            f"/workspace/{boundary.one_over}",
            max_segment_bytes=boundary.limit,
        )


@given(boundary=utf8_boundaries(min_limit=9, max_limit=20))
def test_path_utf8_exact_and_one_over_boundaries(boundary: Utf8Boundary) -> None:
    prefix_bytes = len(b"/workspace/")
    exact_limit = prefix_bytes + len(boundary.exact.encode("utf-8"))
    exact = SandboxPath.resolve(
        f"/workspace/{boundary.exact}",
        max_path_bytes=exact_limit,
    )

    assert len(exact.value.encode("utf-8")) == exact_limit
    with pytest.raises(InvalidPathError, match="path"):
        SandboxPath.resolve(
            f"/workspace/{boundary.one_over}",
            max_path_bytes=exact_limit,
        )


@given(
    parent_segments=path_segments(max_size=3),
    child_segment=safe_segments(max_size=6),
)
def test_ancestor_relationship_is_a_strict_segment_prefix(
    parent_segments: tuple[str, ...],
    child_segment: str,
) -> None:
    parent = SandboxPath.resolve(_absolute_source(parent_segments, False))
    child = parent.join(child_segment)

    assert parent.is_ancestor_of(child)
    assert not parent.is_ancestor_of(parent)
    assert not child.is_ancestor_of(parent)
    assert not SandboxPath.resolve("/workspace/a").is_ancestor_of(
        SandboxPath.resolve("/workspace/ab")
    )
