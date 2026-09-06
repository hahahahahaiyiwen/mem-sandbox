from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from agents.sandbox import Manifest
from agents.sandbox.entries import (
    BaseEntry,
    Dir,
    File,
    GitRepo,
    InContainerMountStrategy,
    LocalDir,
    LocalFile,
    RcloneMountPattern,
    S3Mount,
)
from agents.sandbox.errors import InvalidManifestPathError
from agents.sandbox.manifest import Environment
from agents.sandbox.session import SandboxSession as OpenAISandboxSession
from agents.sandbox.types import FileMode, Group, Permissions, User
from agents.sandbox.workspace_paths import SandboxPathGrant

from mem_sandbox.integrations.openai_agents import (
    InMemorySandboxClient,
    InMemorySandboxClientOptions,
    InMemorySandboxSession,
)
from mem_sandbox.service import (
    CreateSandboxRequest,
    ResumeSandboxRequest,
    SandboxHandle,
    SandboxService,
)
from mem_sandbox.session import (
    CreateDirectoryRequest,
    ReadBytesRequest,
    SandboxSession,
)
from mem_sandbox.workspace import (
    FileSizeLimitExceededError,
    NodeLimitExceededError,
    WorkspaceLimits,
    WorkspaceSizeLimitExceededError,
)

from .support import RecordingService, create_service_bundle


class FailingDirectorySession:
    async def create_directory(self, request: CreateDirectoryRequest) -> None:
        _ = request
        raise RuntimeError("directory materialization failed")


class FailingMaterializationService:
    def __init__(self) -> None:
        self.handle = SandboxHandle(UUID("11111111-1111-1111-1111-111111111111"))
        self.deleted_handles: list[SandboxHandle] = []

    async def create(self, request: CreateSandboxRequest) -> SandboxHandle:
        _ = request
        return self.handle

    async def get_session(self, handle: SandboxHandle) -> SandboxSession:
        assert handle == self.handle
        return cast(SandboxSession, FailingDirectorySession())

    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle:
        _ = request
        raise AssertionError("resume is not expected")

    async def delete(self, handle: SandboxHandle) -> None:
        self.deleted_handles.append(handle)

    async def close(self) -> None:
        return None


class FailingStagingDeleteService(RecordingService):
    async def delete(self, handle: SandboxHandle) -> None:
        await super().delete(handle)
        if len(self.created_handles) > 1 and handle == self.created_handles[-1]:
            raise RuntimeError("staging cleanup failed")


def _inner(session: OpenAISandboxSession) -> InMemorySandboxSession:
    inner = cast(object, cast(Any, session)._inner)
    assert isinstance(inner, InMemorySandboxSession)
    return inner


@pytest.mark.asyncio
async def test_supported_manifest_preserves_binary_files_nested_directories_and_empty_directories() -> (
    None
):
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    sdk_session = await client.create(
        manifest=Manifest(
            entries={
                "project": Dir(
                    children={
                        "empty": Dir(),
                        "payload.bin": File(content=b"\x00\xffpayload"),
                        "nested": Dir(children={"note.txt": File(content=b"note")}),
                    }
                )
            }
        ),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()
    core = await bundle.service.get_session(SandboxHandle(UUID(inner.state.sandbox_handle)))

    assert (await core.read_bytes(ReadBytesRequest(path="project/payload.bin"))).content == (
        b"\x00\xffpayload"
    )
    assert (
        await core.read_bytes(ReadBytesRequest(path="project/nested/note.txt"))
    ).content == b"note"
    assert [Path(entry.path).name for entry in await inner.ls("/workspace/project")] == [
        "empty",
        "nested",
        "payload.bin",
    ]

    await bundle.service.close()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        (Manifest(root="/different"), "root"),
        (Manifest(environment=Environment(value={"TOKEN": "secret"})), "environment"),
        (Manifest(users=[User(name="agent")]), "users"),
        (Manifest(groups=[Group(name="team", users=[])]), "groups"),
        (
            Manifest(extra_path_grants=(SandboxPathGrant(path="/host"),)),
            "extra_path_grants",
        ),
        (
            Manifest(remote_mount_command_allowlist=["custom"]),
            "remote_mount_command_allowlist",
        ),
        (
            Manifest(entries={"configured.txt": File(content=b"x", ephemeral=True)}),
            "ephemeral",
        ),
        (
            Manifest(
                entries={
                    "configured.txt": File(
                        content=b"x",
                        group=Group(name="team", users=[]),
                    )
                }
            ),
            "group",
        ),
        (
            Manifest(
                entries={
                    "configured": Dir(
                        group=User(name="agent"),
                    )
                }
            ),
            "group",
        ),
        (
            Manifest(
                entries={
                    "configured.txt": File(
                        content=b"x",
                        permissions=Permissions(
                            owner=FileMode.READ,
                            group=FileMode.NONE,
                            other=FileMode.NONE,
                        ),
                    )
                }
            ),
            "permissions",
        ),
        (
            Manifest(
                entries={
                    "configured": Dir(
                        permissions=Permissions(
                            owner=FileMode.READ,
                            group=FileMode.NONE,
                            other=FileMode.NONE,
                        )
                    )
                }
            ),
            "permissions",
        ),
        (Manifest(entries={"local": LocalFile(src=Path("host.txt"))}), "local_file"),
        (Manifest(entries={"local": LocalDir(src=Path("host"))}), "local_dir"),
        (
            Manifest(
                entries={
                    "repo": GitRepo(
                        repo="owner/repo",
                        ref="main",
                    )
                }
            ),
            "git_repo",
        ),
        (
            Manifest(
                entries={
                    "mount": S3Mount(
                        bucket="bucket",
                        mount_strategy=InContainerMountStrategy(pattern=RcloneMountPattern()),
                    )
                }
            ),
            "s3_mount",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_manifest_features_are_rejected_before_service_allocation(
    manifest: Manifest,
    message: str,
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)

    with pytest.raises(ValueError, match=message):
        await client.create(
            manifest=manifest,
            options=InMemorySandboxClientOptions(),
        )

    assert service.create_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_unsupported_nested_entry_is_found_before_any_workspace_mutation() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)
    manifest = Manifest(
        entries={
            "safe": Dir(
                children={
                    "ok.txt": File(content=b"ok"),
                    "host.txt": LocalFile(src=Path("host.txt")),
                }
            )
        }
    )

    with pytest.raises(ValueError, match="local_file"):
        await client.create(
            manifest=manifest,
            options=InMemorySandboxClientOptions(),
        )

    assert service.create_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_post_allocation_manifest_failure_deletes_the_owned_sandbox() -> None:
    service = FailingMaterializationService()
    client = InMemorySandboxClient(cast(SandboxService, service))

    with pytest.raises(RuntimeError, match="directory materialization failed"):
        await client.create(
            manifest=Manifest(entries={"empty": Dir()}),
            options=InMemorySandboxClientOptions(),
        )

    assert service.deleted_handles == [service.handle]


@pytest.mark.asyncio
async def test_exposed_ports_are_rejected_by_provider_options_before_allocation() -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)

    with pytest.raises(ValueError, match="ports"):
        await client.create(
            manifest=Manifest(),
            options=InMemorySandboxClientOptions(exposed_ports=(8080,)),
        )

    assert service.create_calls == 0
    await service.close()


def test_profile_version_is_explicit_and_serialized() -> None:
    options = InMemorySandboxClientOptions()

    assert options.manifest_profile_version == 1
    assert options.model_dump(mode="json")["manifest_profile_version"] == 1


@pytest.mark.asyncio
async def test_manifest_start_never_executes_posix_metadata_or_account_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)

    async def forbidden_exec(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    sdk_session = await client.create(
        manifest=Manifest(
            entries={
                "nested": Dir(
                    children={
                        "empty": Dir(),
                        "file.bin": File(content=b"data"),
                    }
                )
            }
        ),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)
    monkeypatch.setattr(inner, "_exec_internal", forbidden_exec)
    await sdk_session.start()

    assert (await sdk_session.read(Path("/workspace/nested/file.bin"))).read() == b"data"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_live_manifest_application_uses_native_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    client = InMemorySandboxClient(bundle.service)
    sdk_session = await client.create(
        manifest=Manifest(
            entries={
                "app": Dir(children={"data.bin": File(content=b"\x00\x01")}),
            }
        ),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)

    async def forbidden_exec(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(inner, "_exec_internal", forbidden_exec)
    await sdk_session.apply_manifest()

    assert (await sdk_session.read(Path("/workspace/app/data.bin"))).read() == b"\x00\x01"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_live_manifest_application_revalidates_mutated_state_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(entries={"keep.bin": File(content=b"keep")}),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)
    inner.state.manifest = Manifest(
        entries={
            "new.bin": File(content=b"new"),
            "nested": Dir(children={"host.bin": LocalFile(src=Path("host.bin"))}),
        }
    )

    async def forbidden_exec(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(inner, "_exec_internal", forbidden_exec)
    with pytest.raises(ValueError, match="local_file"):
        await sdk_session.apply_manifest()

    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"keep"
    assert [entry.path for entry in await sdk_session.ls("/workspace")] == ["/workspace/keep.bin"]
    await bundle.service.close()


@pytest.mark.parametrize(
    "entries",
    [
        {
            "a": File(content=b"first"),
            "./a": File(content=b"second"),
        },
        {
            "a": File(content=b"parent"),
            "a/b": File(content=b"child"),
        },
        {
            ".": File(content=b"root"),
        },
    ],
)
@pytest.mark.asyncio
async def test_conflicting_supported_manifest_paths_are_rejected_before_allocation(
    entries: dict[str | Path, BaseEntry],
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)
    client = InMemorySandboxClient(service)

    with pytest.raises(InvalidManifestPathError):
        await client.create(
            manifest=Manifest(entries=entries),
            options=InMemorySandboxClientOptions(),
        )

    assert service.create_calls == 0
    await service.close()


@pytest.mark.parametrize(
    ("manifest", "limits", "expected_error"),
    [
        (
            Manifest(entries={"large.bin": File(content=b"12345")}),
            WorkspaceLimits(max_file_bytes=4, max_total_bytes=8),
            FileSizeLimitExceededError,
        ),
        (
            Manifest(
                entries={
                    "first.bin": File(content=b"123"),
                    "second.bin": File(content=b"456"),
                }
            ),
            WorkspaceLimits(max_file_bytes=4, max_total_bytes=5),
            WorkspaceSizeLimitExceededError,
        ),
        (
            Manifest(entries={"a/b/c.bin": File(content=b"x")}),
            WorkspaceLimits(max_nodes=3),
            NodeLimitExceededError,
        ),
    ],
)
@pytest.mark.asyncio
async def test_manifest_quota_failures_are_rejected_before_service_allocation(
    manifest: Manifest,
    limits: WorkspaceLimits,
    expected_error: type[Exception],
) -> None:
    bundle = create_service_bundle()
    service = RecordingService(bundle.service)

    with pytest.raises(expected_error):
        await InMemorySandboxClient(service).create(
            manifest=manifest,
            options=InMemorySandboxClientOptions(workspace_limits=limits),
        )

    assert service.create_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_live_manifest_quota_failure_does_not_partially_mutate_workspace() -> None:
    bundle = create_service_bundle()
    sdk_session = await InMemorySandboxClient(bundle.service).create(
        manifest=Manifest(entries={"keep.bin": File(content=b"k")}),
        options=InMemorySandboxClientOptions(
            workspace_limits=WorkspaceLimits(max_file_bytes=4, max_total_bytes=4)
        ),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()
    inner.state.manifest = Manifest(
        entries={
            "first.bin": File(content=b"12"),
            "second.bin": File(content=b"34"),
        }
    )

    with pytest.raises(WorkspaceSizeLimitExceededError):
        await sdk_session.apply_manifest()

    assert [entry.path for entry in await sdk_session.ls("/workspace")] == ["/workspace/keep.bin"]
    assert (await sdk_session.read(Path("/workspace/keep.bin"))).read() == b"k"
    await bundle.service.close()


@pytest.mark.asyncio
async def test_live_manifest_staging_cleanup_failure_prevents_publication() -> None:
    bundle = create_service_bundle()
    service = FailingStagingDeleteService(bundle.service)
    sdk_session = await InMemorySandboxClient(service).create(
        manifest=Manifest(entries={"keep.bin": File(content=b"keep")}),
        options=InMemorySandboxClientOptions(),
    )
    inner = _inner(sdk_session)
    await sdk_session.start()
    inner.state.manifest = Manifest(entries={"new.bin": File(content=b"new")})

    with pytest.raises(RuntimeError, match="staging cleanup failed"):
        await sdk_session.apply_manifest()

    assert [entry.path for entry in await sdk_session.ls("/workspace")] == ["/workspace/keep.bin"]
    await service.close()
