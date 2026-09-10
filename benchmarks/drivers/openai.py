"""OpenAI sandbox-session and four-tool capability validation drivers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, cast

from agents.sandbox import Manifest
from agents.sandbox.entries import BaseEntry, Dir, File
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from agents.tool import FunctionTool

from benchmarks.drivers.core import CoreOperations
from benchmarks.support import create_validation_service_bundle
from benchmarks.validation import (
    ExecuteOutcome,
    PatchedFileOutcome,
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
from mem_sandbox.core import SnapshotId
from mem_sandbox.service import SandboxNotFound
from mem_sandbox.snapshots import SnapshotRef
from mem_sandbox_openai_agents import (
    InMemorySandboxCapability,
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSessionState,
    InMemorySandboxSnapshotSpec,
)
from mem_sandbox_openai_agents.adapter import resolve_in_memory_sandbox_session


class _OpenAIValidationDriver:
    name: ClassVar[str] = "openai_sandbox"

    def __init__(self, *, capability: bool = False) -> None:
        self._bundle = create_validation_service_bundle()
        self._client = InMemorySandboxClient(
            self._bundle.service,
            snapshot_store=self._bundle.snapshot_store,
            clock=self._bundle.clock,
        )
        self._capability = capability

    async def create(self, request: ValidationCreateRequest) -> ValidationSession:
        sdk_session = await self._client.create(
            snapshot=InMemorySandboxSnapshotSpec(),
            manifest=Manifest(entries=_manifest_entries(request)),
            options=InMemorySandboxClientOptions(owner_id=request.owner_id),
        )
        await sdk_session.start()
        return self._wrap(sdk_session)

    async def resume(
        self,
        snapshot: ValidationSnapshot,
        owner_id: str,
    ) -> ValidationSession:
        _ = owner_id
        state = cast(InMemorySandboxSessionState, snapshot.token).model_copy(deep=True)
        sdk_session = await self._client.resume(state)
        await sdk_session.start()
        return self._wrap(sdk_session)

    async def delete(self, session: ValidationSession) -> bool:
        wrapped = cast(_OpenAIValidationSession, session)
        handle = wrapped.provider.handle
        deletion_error: BaseException | None = None
        try:
            await self._client.delete(wrapped.sdk_session)
        except BaseException as error:
            deletion_error = error
        try:
            await wrapped.sdk_session.aclose()
        except BaseException as cleanup_error:
            if deletion_error is None:
                raise
            deletion_error.add_note(f"secondary SDK session cleanup failure: {cleanup_error}")
        if deletion_error is not None:
            raise deletion_error
        try:
            await self._bundle.service.get_session(handle)
        except SandboxNotFound:
            return True
        return False

    async def delete_snapshot(self, snapshot: ValidationSnapshot) -> None:
        state = cast(InMemorySandboxSessionState, snapshot.token)
        await self._bundle.snapshot_store.delete(SnapshotRef(SnapshotId.parse(state.snapshot.id)))

    async def snapshot_count(self) -> int:
        return (await self._bundle.snapshot_store.stats()).snapshot_count

    async def close(self) -> None:
        await self._bundle.service.close()

    def _wrap(self, sdk_session: OpenAISandboxSession) -> _OpenAIValidationSession:
        if self._capability:
            return _OpenAICapabilityValidationSession(sdk_session)
        return _OpenAIValidationSession(sdk_session)


class OpenAISandboxValidationDriver(_OpenAIValidationDriver):
    name: ClassVar[str] = "openai_sandbox"


class OpenAICapabilityValidationDriver(_OpenAIValidationDriver):
    name: ClassVar[str] = "openai_capability"

    def __init__(self) -> None:
        super().__init__(capability=True)


class _OpenAIValidationSession:
    def __init__(
        self,
        sdk_session: OpenAISandboxSession,
    ) -> None:
        self.sdk_session = sdk_session
        self.provider = resolve_in_memory_sandbox_session(sdk_session)
        self._operations = CoreOperations(
            self.provider.core_session,
            require_available=self.provider.require_available,
        )

    @property
    def identity(self) -> str:
        return str(self.provider.core_session.session_id)

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
        await self.sdk_session.stop()
        state = self.provider.state.model_copy(deep=True)
        revision = _required(state.workspace_archive_revision, "workspace archive revision")
        root_hash = _required(state.workspace_archive_root_hash, "workspace archive root hash")
        environment = tuple((item.name, item.value) for item in state.approved_environment)
        return ValidationSnapshot(
            token=state,
            revision=revision,
            state_hash=normalized_state_hash(
                revision=revision,
                workspace_root_hash=root_hash,
                cwd=state.cwd,
                approved_environment=environment,
            ),
            workspace_root_hash=root_hash,
            cwd=state.cwd,
            approved_environment=environment,
        )

    async def state(self) -> ValidationSessionState:
        archive = await self.provider.core_session.export_portable_archive()
        return ValidationSessionState(
            identity=self.identity,
            revision=archive.workspace_revision.value,
            cwd=self.provider.core_session.cwd.value,
            approved_environment=tuple(
                (item.name, item.value) for item in self.provider.core_session.environment.values
            ),
        )

    async def close(self) -> None:
        await self.sdk_session.aclose()


class _OpenAICapabilityValidationSession(_OpenAIValidationSession):
    def __init__(
        self,
        sdk_session: OpenAISandboxSession,
    ) -> None:
        super().__init__(sdk_session)
        capability = InMemorySandboxCapability()
        capability.bind(sdk_session)
        self._tools: dict[str, FunctionTool] = {}
        for tool in capability.tools():
            if not isinstance(tool, FunctionTool):
                raise TypeError("MemSandbox capability exposed a non-function tool")
            self._tools[tool.name] = tool

    async def execute(self, command: str) -> ExecuteOutcome | ValidationError:
        output = await self._invoke("execute", {"command": command})
        if not output["ok"]:
            return _tool_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return ExecuteOutcome(
            revision=cast(int, metadata["workspace_revision"]),
            exit_code=cast(int, result["exit_code"]),
            failure_code=cast(str | None, result["failure_code"]),
            stdout=cast(str, result["stdout"]),
            stderr=cast(str, result["stderr"]),
            resulting_cwd=cast(str, result["resulting_cwd"]),
            environment_changes=tuple(
                (cast(str, item["name"]), cast(str | None, item["value"]))
                for item in cast(list[dict[str, Any]], result["environment_changes"])
            ),
        )

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ReadOutcome | ValidationError:
        output = await self._invoke(
            "read_file",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )
        if not output["ok"]:
            return _tool_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return ReadOutcome(
            revision=cast(int, metadata["workspace_revision"]),
            content=cast(str, result["content"]),
            content_hash=cast(str, result["content_hash"]),
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
        output = await self._invoke(
            "write_file",
            {
                "path": path,
                "content": content,
                "write_condition": write_condition,
                "expected_hash": expected_hash,
                "create_parents": create_parents,
            },
        )
        if not output["ok"]:
            return _tool_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return WriteOutcome(
            revision=cast(int, metadata["workspace_revision"]),
            created=cast(bool, result["created"]),
            changed=cast(bool, result["changed"]),
            previous_hash=cast(str | None, result["previous_hash"]),
            current_hash=cast(str | None, result["current_hash"]),
        )

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: tuple[tuple[str, str], ...],
    ) -> PatchOutcome | ValidationError:
        output = await self._invoke(
            "apply_patch",
            {
                "patch": patch,
                "expected_hashes": [
                    {"path": path, "content_hash": content_hash}
                    for path, content_hash in expected_hashes
                ],
            },
        )
        if not output["ok"]:
            return _tool_error(output)
        result = cast(dict[str, Any], output["result"])
        metadata = cast(dict[str, Any], result["metadata"])
        return PatchOutcome(
            revision=cast(int, metadata["workspace_revision"]),
            files=tuple(
                PatchedFileOutcome(
                    path=cast(str, item["path"]),
                    previous_hash=cast(str, item["previous_hash"]),
                    current_hash=cast(str, item["current_hash"]),
                )
                for item in cast(list[dict[str, Any]], result["files"])
            ),
        )

    async def _invoke(
        self,
        name: str,
        payload: dict[str, object],
    ) -> dict[str, Any]:
        rendered: object = await self._tools[name].on_invoke_tool(
            cast(Any, None),
            json.dumps(payload),
        )
        if not isinstance(rendered, str):
            raise TypeError("capability tool returned a non-text result")
        parsed: object = json.loads(rendered)
        if not isinstance(parsed, dict):
            raise TypeError("capability tool returned a non-object result")
        return cast(dict[str, Any], parsed)


def _tool_error(output: dict[str, Any]) -> ValidationError:
    error = cast(dict[str, Any], output["error"])
    return ValidationError(cast(str, error["category"]), cast(str, error["code"]))


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        raise RuntimeError(f"{name} was not persisted")
    return value


def _manifest_entries(request: ValidationCreateRequest) -> dict[str | Path, BaseEntry]:
    root: dict[str | Path, BaseEntry] = {}
    for validation_file in request.files:
        parts = validation_file.path.removeprefix("/workspace/").split("/")
        current: dict[str | Path, BaseEntry] = root
        for segment in parts[:-1]:
            entry = current.get(segment)
            if entry is None:
                directory = Dir(children={})
                current[segment] = directory
                current = directory.children
                continue
            if not isinstance(entry, Dir):
                raise ValueError(f"profile path conflicts with file: {validation_file.path}")
            current = entry.children
        current[parts[-1]] = File(content=validation_file.content)
    return root
