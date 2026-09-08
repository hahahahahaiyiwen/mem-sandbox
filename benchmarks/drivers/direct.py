"""Direct public service/session product-validation driver."""

from __future__ import annotations

from typing import ClassVar, cast

from benchmarks.drivers.core import CoreOperations
from benchmarks.support import ValidationServiceBundle, create_validation_service_bundle
from benchmarks.validation import (
    EnvironmentItems,
    ExecuteOutcome,
    PatchOutcome,
    ReadOutcome,
    ValidationCreateRequest,
    ValidationError,
    ValidationSession,
    ValidationSessionState,
    ValidationSnapshot,
    WriteCondition,
    WriteOutcome,
    normalized_state_hash,
)
from mem_sandbox.command_executor import EnvironmentValue
from mem_sandbox.service import (
    CreateSandboxRequest,
    OwnerId,
    ResumeSandboxRequest,
    SandboxHandle,
    SandboxNotFound,
    WorkspaceSeedFile,
)
from mem_sandbox.session import CreateSnapshotRequest, SandboxSession
from mem_sandbox.snapshots import SnapshotRef


class DirectValidationSession:
    def __init__(
        self,
        bundle: ValidationServiceBundle,
        handle: SandboxHandle,
        session: SandboxSession,
    ) -> None:
        self._bundle = bundle
        self.handle = handle
        self.session = session
        self._operations = CoreOperations(session)

    @property
    def identity(self) -> str:
        return str(self.session.session_id)

    async def execute(self, command: str) -> ExecuteOutcome | ValidationError:
        return await self._operations.execute(command)

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ReadOutcome | ValidationError:
        return await self._operations.read_file(path, start_line=start_line, end_line=end_line)

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: WriteCondition,
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> WriteOutcome | ValidationError:
        return await self._operations.write_file(
            path,
            content,
            write_condition=write_condition,
            expected_hash=expected_hash,
            create_parents=create_parents,
        )

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: tuple[tuple[str, str], ...],
    ) -> PatchOutcome | ValidationError:
        return await self._operations.apply_patch(patch, expected_hashes)

    async def snapshot(self) -> ValidationSnapshot:
        result = await self.session.create_snapshot(CreateSnapshotRequest())
        persisted = await self._bundle.snapshot_gateway.load(result.snapshot_ref)
        state = self._bundle.snapshot_codec.decode(persisted)
        environment = _environment_items(state.approved_environment.values)
        root_hash = state.workspace.root_hash.value
        revision = state.workspace.workspace_revision.value
        return ValidationSnapshot(
            token=result.snapshot_ref,
            revision=revision,
            state_hash=normalized_state_hash(
                revision=revision,
                workspace_root_hash=root_hash,
                cwd=state.cwd.value,
                approved_environment=environment,
            ),
            workspace_root_hash=root_hash,
            cwd=state.cwd.value,
            approved_environment=environment,
        )

    async def state(self) -> ValidationSessionState:
        archive = await self.session.export_portable_archive()
        return ValidationSessionState(
            identity=self.identity,
            revision=archive.workspace_revision.value,
            cwd=self.session.cwd.value,
            approved_environment=_environment_items(self.session.environment.values),
        )

    async def close(self) -> None:
        await self.session.close()


class DirectValidationDriver:
    name: ClassVar[str] = "direct"

    def __init__(self) -> None:
        self._bundle = create_validation_service_bundle()

    async def create(self, request: ValidationCreateRequest) -> ValidationSession:
        handle = await self._bundle.service.create(
            CreateSandboxRequest(
                owner_id=OwnerId(request.owner_id),
                initial_files=tuple(
                    WorkspaceSeedFile(file.path, file.content) for file in request.files
                ),
            )
        )
        session = await self._bundle.service.get_session(handle)
        return DirectValidationSession(self._bundle, handle, session)

    async def resume(
        self,
        snapshot: ValidationSnapshot,
        owner_id: str,
    ) -> ValidationSession:
        handle = await self._bundle.service.resume(
            ResumeSandboxRequest(
                owner_id=OwnerId(owner_id),
                snapshot_ref=cast(SnapshotRef, snapshot.token),
            )
        )
        session = await self._bundle.service.get_session(handle)
        return DirectValidationSession(self._bundle, handle, session)

    async def delete(self, session: ValidationSession) -> bool:
        direct = cast(DirectValidationSession, session)
        await self._bundle.service.delete(direct.handle)
        try:
            await self._bundle.service.get_session(direct.handle)
        except SandboxNotFound:
            return True
        return False

    async def delete_snapshot(self, snapshot: ValidationSnapshot) -> None:
        await self._bundle.snapshot_store.delete(cast(SnapshotRef, snapshot.token))

    async def snapshot_count(self) -> int:
        return (await self._bundle.snapshot_store.stats()).snapshot_count

    async def close(self) -> None:
        await self._bundle.service.close()


def _environment_items(values: tuple[EnvironmentValue, ...]) -> EnvironmentItems:
    return tuple((value.name, value.value) for value in values)
