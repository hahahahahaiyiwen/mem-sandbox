"""Framework-neutral product-validation contracts and reference scenario."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import ClassVar, Literal, Protocol

type WriteCondition = Literal[
    "any_current_state",
    "path_must_not_exist",
    "content_hash_must_equal",
]
type EnvironmentItems = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ValidationFile:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ValidationCreateRequest:
    owner_id: str
    files: tuple[ValidationFile, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationError:
    category: str
    code: str


@dataclass(frozen=True, slots=True)
class ExecuteOutcome:
    revision: int
    exit_code: int
    failure_code: str | None
    stdout: str
    stderr: str
    resulting_cwd: str
    environment_changes: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class ReadOutcome:
    revision: int
    content: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    revision: int
    created: bool
    changed: bool
    previous_hash: str | None
    current_hash: str | None


@dataclass(frozen=True, slots=True)
class PatchedFileOutcome:
    path: str
    previous_hash: str
    current_hash: str


@dataclass(frozen=True, slots=True)
class PatchOutcome:
    revision: int
    files: tuple[PatchedFileOutcome, ...]


@dataclass(frozen=True, slots=True)
class ValidationSessionState:
    identity: str = field(compare=False, repr=False)
    revision: int
    cwd: str
    approved_environment: EnvironmentItems


@dataclass(frozen=True, slots=True)
class ValidationSnapshot:
    token: object = field(compare=False, repr=False)
    revision: int
    state_hash: str
    workspace_root_hash: str
    cwd: str
    approved_environment: EnvironmentItems


type OperationOutcome = ExecuteOutcome | ReadOutcome | WriteOutcome | PatchOutcome | ValidationError


class ValidationSession(Protocol):
    @property
    def identity(self) -> str: ...

    async def execute(self, command: str) -> ExecuteOutcome | ValidationError: ...

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ReadOutcome | ValidationError: ...

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        write_condition: WriteCondition,
        expected_hash: str | None = None,
        create_parents: bool = False,
    ) -> WriteOutcome | ValidationError: ...

    async def apply_patch(
        self,
        patch: str,
        expected_hashes: tuple[tuple[str, str], ...],
    ) -> PatchOutcome | ValidationError: ...

    async def snapshot(self) -> ValidationSnapshot: ...

    async def state(self) -> ValidationSessionState: ...

    async def close(self) -> None: ...


class ProductValidationDriver(Protocol):
    name: ClassVar[str]

    async def create(self, request: ValidationCreateRequest) -> ValidationSession: ...

    async def resume(
        self,
        snapshot: ValidationSnapshot,
        owner_id: str,
    ) -> ValidationSession: ...

    async def delete(self, session: ValidationSession) -> bool: ...

    async def delete_snapshot(self, snapshot: ValidationSnapshot) -> None: ...

    async def snapshot_count(self) -> int: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TraceEvent:
    name: str
    payload_json: str


@dataclass(frozen=True, slots=True)
class StatefulScenarioTrace:
    driver: str
    events: tuple[TraceEvent, ...]
    schema_version: int = 1

    @property
    def correctness_checksum(self) -> str:
        return sha256(self.equivalence_json().encode("utf-8")).hexdigest()

    def equivalence_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "events": [
                    {"name": event.name, "payload": json.loads(event.payload_json)}
                    for event in self.events
                ],
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


async def run_stateful_reference_scenario(
    driver: ProductValidationDriver,
) -> StatefulScenarioTrace:
    """Run the common create-to-cleanup scenario through one product driver."""
    events: list[TraceEvent] = []
    snapshots: list[ValidationSnapshot] = []

    def record(name: str, value: object) -> None:
        events.append(TraceEvent(name, _canonical_json(value)))

    try:
        source = await driver.create(ValidationCreateRequest("scenario-source"))
        source_identity = source.identity
        record("create", _state_without_identity(await source.state()))

        first_write = _require_write(
            await source.write_file(
                "/workspace/project/app.txt",
                "alpha\nbeta\ngamma\n",
                write_condition="path_must_not_exist",
                create_parents=True,
            )
        )
        record("write_app", first_write)
        record(
            "write_second",
            _require_write(
                await source.write_file(
                    "/workspace/project/second.txt",
                    "second\n",
                    write_condition="path_must_not_exist",
                )
            ),
        )
        record(
            "inspect",
            _require_execute(
                await source.execute("cd /workspace/project; export MODE=base; pwd; cat app.txt")
            ),
        )
        bounded = _require_read(await source.read_file("app.txt", start_line=2, end_line=3))
        record("bounded_read", bounded)
        patched = _require_patch(
            await source.apply_patch(
                (
                    "--- /workspace/project/app.txt\n"
                    "+++ /workspace/project/app.txt\n"
                    "@@ -1,3 +1,3 @@\n"
                    " alpha\n"
                    "-beta\n"
                    "+delta\n"
                    " gamma\n"
                ),
                (("app.txt", bounded.content_hash),),
            )
        )
        record("patch", patched)
        record(
            "stale_write",
            await source.write_file(
                "app.txt",
                "stale\n",
                write_condition="content_hash_must_equal",
                expected_hash=bounded.content_hash,
            ),
        )
        after_stale = _require_read(await source.read_file("app.txt"))
        record("after_stale", after_stale)
        record(
            "unsupported_execute",
            _require_execute(await source.execute('python -c \'open("host-canary", "w")\'')),
        )

        checkpoint = await source.snapshot()
        snapshots.append(checkpoint)
        record("checkpoint", _snapshot_without_token(checkpoint))
        await source.close()
        record("closed_read", await source.read_file("app.txt"))
        record("source_cleanup", {"missing": await driver.delete(source)})

        resumed = await driver.resume(checkpoint, "scenario-resumed")
        record(
            "resume",
            {
                "identity_changed": resumed.identity != source_identity,
                "state": _state_without_identity(await resumed.state()),
            },
        )
        record("resumed_app", _require_read(await resumed.read_file("app.txt")))
        continued_write = _require_write(
            await resumed.write_file(
                "continued.txt",
                "one\ntwo\n",
                write_condition="path_must_not_exist",
            )
        )
        record("continued_write", continued_write)
        assert continued_write.current_hash is not None
        record(
            "continued_patch",
            _require_patch(
                await resumed.apply_patch(
                    (
                        "--- /workspace/project/continued.txt\n"
                        "+++ /workspace/project/continued.txt\n"
                        "@@ -1,2 +1,2 @@\n"
                        " one\n"
                        "-two\n"
                        "+three\n"
                    ),
                    (("continued.txt", continued_write.current_hash),),
                )
            ),
        )
        record(
            "continued_execute",
            _require_execute(await resumed.execute("cat continued.txt")),
        )
        resumed_checkpoint = await resumed.snapshot()
        snapshots.append(resumed_checkpoint)
        record("resumed_checkpoint", _snapshot_without_token(resumed_checkpoint))

        first_fork = await driver.resume(checkpoint, "scenario-first-fork")
        second_fork = await driver.resume(checkpoint, "scenario-second-fork")
        record(
            "fork_identities",
            {
                "all_distinct": len(
                    {
                        source_identity,
                        resumed.identity,
                        first_fork.identity,
                        second_fork.identity,
                    }
                )
                == 4
            },
        )
        record(
            "first_fork_write",
            _require_write(
                await first_fork.write_file(
                    "fork.txt",
                    "first\n",
                    write_condition="path_must_not_exist",
                )
            ),
        )
        record(
            "second_fork_write",
            _require_write(
                await second_fork.write_file(
                    "fork.txt",
                    "second\n",
                    write_condition="path_must_not_exist",
                )
            ),
        )
        record("first_fork_read", _require_read(await first_fork.read_file("fork.txt")))
        record("second_fork_read", _require_read(await second_fork.read_file("fork.txt")))
        record("resumed_fork_absent", await resumed.read_file("fork.txt"))
        record(
            "live_cleanup",
            {
                "first_fork": await driver.delete(first_fork),
                "second_fork": await driver.delete(second_fork),
                "resumed": await driver.delete(resumed),
            },
        )
        for snapshot in snapshots:
            await driver.delete_snapshot(snapshot)
        record("snapshot_cleanup", {"count": await driver.snapshot_count()})
    finally:
        await driver.close()

    return StatefulScenarioTrace(driver.name, tuple(events))


def normalized_state_hash(
    *,
    revision: int,
    workspace_root_hash: str,
    cwd: str,
    approved_environment: EnvironmentItems,
) -> str:
    return sha256(
        _canonical_json(
            {
                "revision": revision,
                "workspace_root_hash": workspace_root_hash,
                "cwd": cwd,
                "approved_environment": approved_environment,
            }
        ).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: object) -> str:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _state_without_identity(state: ValidationSessionState) -> dict[str, object]:
    return {
        "revision": state.revision,
        "cwd": state.cwd,
        "approved_environment": state.approved_environment,
    }


def _snapshot_without_token(snapshot: ValidationSnapshot) -> dict[str, object]:
    return {
        "revision": snapshot.revision,
        "state_hash": snapshot.state_hash,
        "workspace_root_hash": snapshot.workspace_root_hash,
        "cwd": snapshot.cwd,
        "approved_environment": snapshot.approved_environment,
    }


def _require_execute(value: ExecuteOutcome | ValidationError) -> ExecuteOutcome:
    if isinstance(value, ValidationError):
        raise AssertionError(f"execute failed: {value.category}/{value.code}")
    return value


def _require_read(value: ReadOutcome | ValidationError) -> ReadOutcome:
    if isinstance(value, ValidationError):
        raise AssertionError(f"read failed: {value.category}/{value.code}")
    return value


def _require_write(value: WriteOutcome | ValidationError) -> WriteOutcome:
    if isinstance(value, ValidationError):
        raise AssertionError(f"write failed: {value.category}/{value.code}")
    return value


def _require_patch(value: PatchOutcome | ValidationError) -> PatchOutcome:
    if isinstance(value, ValidationError):
        raise AssertionError(f"patch failed: {value.category}/{value.code}")
    return value
