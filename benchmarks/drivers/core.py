"""Normalized four-operation projection over a public core session."""

from __future__ import annotations

from collections.abc import Callable

from benchmarks.validation import (
    ExecuteOutcome,
    PatchedFileOutcome,
    PatchOutcome,
    ReadOutcome,
    ValidationError,
    WriteCondition,
    WriteOutcome,
)
from mem_sandbox.core import SandboxError
from mem_sandbox.session import (
    ApplyPatchRequest,
    ReadFileRequest,
    SandboxSession,
    SessionExecuteRequest,
    SessionExpectedFileHash,
    WriteFileRequest,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ContentHashMustEqual,
    PathMustNotExist,
)


class CoreOperations:
    def __init__(
        self,
        session: SandboxSession,
        *,
        require_available: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._require_available = require_available

    async def execute(self, command: str) -> ExecuteOutcome | ValidationError:
        try:
            self._require()
            result = await self._session.execute(SessionExecuteRequest(command=command))
        except SandboxError as error:
            return _error(error)
        return ExecuteOutcome(
            revision=result.metadata.workspace_revision.value,
            exit_code=result.exit_code,
            failure_code=None if result.failure_code is None else result.failure_code.value,
            stdout=result.stdout,
            stderr=result.stderr,
            resulting_cwd=result.resulting_cwd.value,
            environment_changes=tuple(
                (change.name, change.value) for change in result.environment_changes
            ),
        )

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ReadOutcome | ValidationError:
        try:
            self._require()
            result = await self._session.read_file(
                ReadFileRequest(path=path, start_line=start_line, end_line=end_line)
            )
        except SandboxError as error:
            return _error(error)
        return ReadOutcome(
            revision=result.metadata.workspace_revision.value,
            content=result.content,
            content_hash=result.content_hash.value,
        )

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: WriteCondition,
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> WriteOutcome | ValidationError:
        if write_condition == "any_current_state":
            precondition = AnyCurrentState()
        elif write_condition == "path_must_not_exist":
            precondition = PathMustNotExist()
        else:
            if expected_hash is None:
                raise ValueError("expected_hash is required for content_hash_must_equal")
            precondition = ContentHashMustEqual(ContentHash(expected_hash))
        try:
            self._require()
            result = await self._session.write_file(
                WriteFileRequest(
                    path=path,
                    content=content,
                    precondition=precondition,
                    create_parents=create_parents,
                )
            )
        except SandboxError as error:
            return _error(error)
        return WriteOutcome(
            revision=result.metadata.workspace_revision.value,
            created=result.created,
            changed=result.changed,
            previous_hash=None if result.previous_hash is None else result.previous_hash.value,
            current_hash=None if result.current_hash is None else result.current_hash.value,
        )

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: tuple[tuple[str, str], ...],
    ) -> PatchOutcome | ValidationError:
        try:
            self._require()
            result = await self._session.apply_patch(
                ApplyPatchRequest(
                    patch=patch,
                    expected_hashes=tuple(
                        SessionExpectedFileHash(path, ContentHash(content_hash))
                        for path, content_hash in expected_hashes
                    ),
                )
            )
        except SandboxError as error:
            return _error(error)
        return PatchOutcome(
            revision=result.metadata.workspace_revision.value,
            files=tuple(
                PatchedFileOutcome(
                    path=item.path.value,
                    previous_hash=item.previous_hash.value,
                    current_hash=item.current_hash.value,
                )
                for item in result.files
            ),
        )

    def _require(self) -> None:
        if self._require_available is not None:
            self._require_available()


def _error(error: SandboxError) -> ValidationError:
    return ValidationError(error.category.value, error.code)
