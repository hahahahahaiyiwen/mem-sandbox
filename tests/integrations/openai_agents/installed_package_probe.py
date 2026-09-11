"""Exercise installed MemSandbox distributions against one OpenAI Agents SDK version."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import sys
from collections.abc import AsyncIterator, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

try:
    from agents import ModelResponse, RunConfig, Runner, Usage
    from agents.agent_output import AgentOutputSchemaBase
    from agents.handoffs import Handoff
    from agents.items import TResponseInputItem, TResponseStreamEvent
    from agents.model_settings import ModelSettings
    from agents.models.interface import Model, ModelTracing
    from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
    from agents.sandbox.entries import Dir, File
    from agents.sandbox.session import SandboxSession as OpenAISandboxSession
    from agents.tool import Tool
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )
    from openai.types.responses.response_prompt_param import ResponsePromptParam

    import mem_sandbox_openai_agents as adapter
    from mem_sandbox.core import OperationKind, SystemClock, SystemUuidGenerator
    from mem_sandbox.events import InMemoryEventSink
    from mem_sandbox.policy import AllowAllPolicyEngine, PolicyDecision, PolicyRequest
    from mem_sandbox.secrets import NoSecretBroker
    from mem_sandbox.service import (
        DefaultSessionFactory,
        InMemorySandboxService,
        InMemoryServiceSnapshotGateway,
        SandboxHandle,
        SandboxNotFound,
    )
    from mem_sandbox.session import SessionPolicyEngine
    from mem_sandbox.snapshots import (
        InMemorySnapshotStore,
        JsonSessionSnapshotCodec,
        SnapshotStoreLimits,
    )
    from mem_sandbox_openai_agents import (
        InMemorySandboxCapability,
        InMemorySandboxClient,
        InMemorySandboxClientOptions,
        InMemorySandboxSessionState,
        InMemorySandboxSnapshotSpec,
    )
except BaseException as error:
    raise RuntimeError(
        "OpenAI Agents SDK compatibility surface "
        f"'public imports and namespace ownership' failed with "
        f"{type(error).__name__}: {error}"
    ) from error

_TOOLS = ["execute", "read_file", "write_file", "apply_patch"]
_CHECKPOINT_PATH = "/workspace/checkpoint.txt"
_RUNNER_RESULT_PATH = "/workspace/from-runner.txt"
_CHECKPOINT_CONTENT = b"state=ready\n"
_RUNNER_RESULT_CONTENT = b"runner=complete\n"

_BRIEF_PATH = "/workspace/source/release-brief.txt"
_DRAFT_PATH = "/workspace/drafts/release-notes.md"
_INSTRUCTIONS_PATH = "/workspace/review/instructions.md"
_REVIEW_PATH = "/workspace/review/findings.md"

_BRIEF_CONTENT = b"""product=Orion Workspace
version=2.4.0
release_date=2026-09-18
minimum_python=3.12
"""
_DRAFT_CONTENT = b"""# Orion Workspace 2.3.0

Orion Workspace 2.3.0 will be released on September 12, 2026.

## Compatibility

Python 3.11 or newer is required.

## Upgrade notes

Back up the workspace before upgrading.
"""
_EXPECTED_DRAFT_CONTENT = b"""# Orion Workspace 2.4.0

Orion Workspace 2.4.0 will be released on September 18, 2026.

## Compatibility

Python 3.12 or newer is required.

## Upgrade notes

Back up the workspace before upgrading.
"""
_INSTRUCTIONS_CONTENT = b"""# Review instructions

Allowed edits:
- Release title and summary sentence.
- Python requirement in Compatibility.

Protected content:
- The Upgrade notes heading and paragraph.
- This instruction file and the source brief.

Required review output:
- Markdown with a status and evidence table.
- Each finding must cite both the draft and source brief paths.
"""
_REVIEW_CONTENT = b"""# Review

Status: approved

| Field | Draft evidence | Source evidence |
| --- | --- | --- |
| Version | `/workspace/drafts/release-notes.md`: `2.4.0` | `/workspace/source/release-brief.txt`: `version=2.4.0` |
| Release date | `/workspace/drafts/release-notes.md`: `September 18, 2026` | `/workspace/source/release-brief.txt`: `release_date=2026-09-18` |
| Python | `/workspace/drafts/release-notes.md`: `3.12` | `/workspace/source/release-brief.txt`: `minimum_python=3.12` |
| Protected content | `/workspace/drafts/release-notes.md`: `Upgrade notes` preserved | `/workspace/review/instructions.md`: protected section requirement |
"""

_SURFACES = [
    "distribution versions",
    "public imports and namespace ownership",
    "client and session lifecycle",
    "capability binding and Runner tool loop",
    "snapshot state serialization and replacement resume",
    "document-review editor and reviewer workflow",
    "SDK session, backend, and service cleanup",
]


class CompatibilityProbeError(RuntimeError):
    """Name the SDK contract surface that failed in an installed environment."""


@contextmanager
def _surface(name: str) -> Generator[None]:
    try:
        yield
    except BaseException as error:
        if isinstance(error, CompatibilityProbeError):
            raise
        raise CompatibilityProbeError(
            f"OpenAI Agents SDK compatibility surface {name!r} failed with "
            f"{type(error).__name__}: {error}"
        ) from error


@dataclass(frozen=True, slots=True)
class ServiceBundle:
    service: InMemorySandboxService
    snapshot_store: InMemorySnapshotStore
    clock: SystemClock


class DocumentReviewPolicyEngine:
    """Protect seeded review inputs while allowing the bounded workflow outputs."""

    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        if request.operation_kind is OperationKind.EXECUTE:
            return PolicyDecision(
                allowed=False,
                reason_code="compatibility_probe_commands_disabled",
                effective_limits=request.requested_limits,
            )
        if request.operation_kind is OperationKind.WRITE_FILE and (
            request.path is None or request.path.value != _REVIEW_PATH
        ):
            return PolicyDecision(
                allowed=False,
                reason_code="compatibility_probe_write_scope",
                effective_limits=request.requested_limits,
            )
        return PolicyDecision(
            allowed=True,
            reason_code="compatibility_probe_operation_allowed",
            effective_limits=request.requested_limits,
        )


def _create_service_bundle(policy_engine: SessionPolicyEngine) -> ServiceBundle:
    clock = SystemClock()
    uuid_generator = SystemUuidGenerator()
    snapshot_codec = JsonSessionSnapshotCodec()
    snapshot_store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=10,
            max_total_payload_bytes=8 * 1024 * 1024,
        ),
        clock=clock,
    )
    session_factory = DefaultSessionFactory(
        policy_engine=policy_engine,
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(
            max_events=200,
            max_payload_bytes=2 * 1024 * 1024,
        ),
        snapshot_codec=snapshot_codec,
        clock=clock,
        uuid_generator=uuid_generator,
    )
    return ServiceBundle(
        service=InMemorySandboxService(
            session_factory=session_factory,
            snapshot_gateway=InMemoryServiceSnapshotGateway(snapshot_store),
            snapshot_decoder=snapshot_codec,
            clock=clock,
            uuid_generator=uuid_generator,
        ),
        snapshot_store=snapshot_store,
        clock=clock,
    )


class DeterministicModel(Model):
    def __init__(
        self,
        stage: Literal["runner", "document-review-editor", "document-review-reviewer"],
    ) -> None:
        self.stage: Literal[
            "runner",
            "document-review-editor",
            "document-review-reviewer",
        ] = stage
        self.calls = 0
        self.tool_names: list[list[str]] = []

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        _ = (
            system_instructions,
            model_settings,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        self.calls += 1
        self.tool_names.append([tool.name for tool in tools])
        output = _scripted_output(self.stage, self.calls, input)
        return ModelResponse(
            output=cast(Any, output),
            usage=Usage(),
            response_id=f"{self.stage}_{self.calls}",
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        _ = (
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        raise NotImplementedError
        yield


def _scripted_output(
    stage: Literal["runner", "document-review-editor", "document-review-reviewer"],
    call: int,
    input: str | list[TResponseInputItem],
) -> list[ResponseFunctionToolCall | ResponseOutputMessage]:
    if stage == "runner":
        if call == 1:
            return [
                _write(
                    "write_runner_result",
                    _RUNNER_RESULT_PATH,
                    _RUNNER_RESULT_CONTENT.decode(),
                )
            ]
        return [_message("Runner tool loop complete.")]

    if stage == "document-review-editor":
        if call == 1:
            return [_read("read_release_brief", _BRIEF_PATH)]
        if call == 2:
            return [_read("read_review_instructions", _INSTRUCTIONS_PATH)]
        if call == 3:
            return [_read("read_release_draft", _DRAFT_PATH)]
        if call == 4:
            return [_document_review_patch(_latest_hash(input))]
        if call == 5:
            return [_read("verify_release_draft", _DRAFT_PATH)]
        return [_message("Release draft corrected.")]

    if call == 1:
        return [_read("review_release_brief", _BRIEF_PATH)]
    if call == 2:
        return [_read("review_instructions", _INSTRUCTIONS_PATH)]
    if call == 3:
        return [_read("review_release_draft", _DRAFT_PATH)]
    if call == 4:
        return [_write("write_document_review", _REVIEW_PATH, _REVIEW_CONTENT.decode())]
    if call == 5:
        return [_read("verify_document_review", _REVIEW_PATH)]
    return [_message("Document review approved.")]


def _tool_call(
    *,
    call_id: str,
    name: str,
    arguments: Mapping[str, object],
) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps(arguments),
        call_id=call_id,
        name=name,
        type="function_call",
        status="completed",
    )


def _write(call_id: str, path: str, content: str) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id=call_id,
        name="write_file",
        arguments={
            "path": path,
            "content": content,
            "write_condition": "path_must_not_exist",
            "create_parents": True,
        },
    )


def _read(call_id: str, path: str) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id=call_id,
        name="read_file",
        arguments={"path": path},
    )


def _document_review_patch(content_hash: str) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id="correct_release_draft",
        name="apply_patch",
        arguments={
            "patch": (
                f"--- {_DRAFT_PATH}\n"
                f"+++ {_DRAFT_PATH}\n"
                "@@ -1,7 +1,7 @@\n"
                "-# Orion Workspace 2.3.0\n"
                "+# Orion Workspace 2.4.0\n"
                " \n"
                "-Orion Workspace 2.3.0 will be released on September 12, 2026.\n"
                "+Orion Workspace 2.4.0 will be released on September 18, 2026.\n"
                " \n"
                " ## Compatibility\n"
                " \n"
                "-Python 3.11 or newer is required.\n"
                "+Python 3.12 or newer is required.\n"
            ),
            "expected_hashes": [{"path": _DRAFT_PATH, "content_hash": content_hash}],
        },
    )


def _message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=text.lower().replace(" ", "_").replace(".", ""),
        content=[
            ResponseOutputText(
                annotations=[],
                text=text,
                type="output_text",
            )
        ],
        role="assistant",
        status="completed",
        type="message",
    )


def _latest_hash(input: str | list[TResponseInputItem]) -> str:
    assert isinstance(input, list)
    for item in reversed(input):
        raw_item = cast(dict[str, Any], item)
        if raw_item.get("type") != "function_call_output":
            continue
        output = json.loads(cast(str, raw_item["output"]))
        if output["ok"] is True:
            return cast(str, output["result"]["content_hash"])
    raise AssertionError("the SDK Runner input did not contain a successful file-read result")


def _provider_state(session: OpenAISandboxSession) -> InMemorySandboxSessionState:
    state = session.state
    assert isinstance(state, InMemorySandboxSessionState)
    return state


def _handle(session: OpenAISandboxSession) -> SandboxHandle:
    return SandboxHandle(UUID(_provider_state(session).sandbox_handle))


async def _assert_deleted(service: InMemorySandboxService, handle: SandboxHandle) -> None:
    try:
        await service.get_session(handle)
    except SandboxNotFound:
        return
    raise AssertionError(f"backend {handle} remained available after cleanup")


async def _retire_session(
    client: InMemorySandboxClient,
    service: InMemorySandboxService,
    session: OpenAISandboxSession,
) -> None:
    handle = _handle(session)
    await session.aclose()
    await client.delete(session)
    await _assert_deleted(service, handle)


async def _cleanup_sessions(
    *,
    client: InMemorySandboxClient,
    service: InMemorySandboxService,
    sessions: list[OpenAISandboxSession],
) -> None:
    cleanup_errors: list[BaseException] = []
    for session in reversed(sessions):
        try:
            await _retire_session(client, service, session)
        except BaseException as error:
            cleanup_errors.append(error)
    try:
        await service.close()
    except BaseException as error:
        cleanup_errors.append(error)
    if cleanup_errors:
        raise BaseExceptionGroup("installed-package compatibility cleanup failed", cleanup_errors)


async def _exercise_lifecycle_and_runner() -> dict[str, object]:
    bundle = _create_service_bundle(AllowAllPolicyEngine())
    client = InMemorySandboxClient(
        bundle.service,
        snapshot_store=bundle.snapshot_store,
        clock=bundle.clock,
    )
    sessions: list[OpenAISandboxSession] = []
    primary: BaseException | None = None
    report: dict[str, object] = {}
    try:
        with _surface("client and session lifecycle"):
            source = await client.create(
                snapshot=InMemorySandboxSnapshotSpec(),
                manifest=Manifest(
                    entries={
                        "checkpoint.txt": File(content=_CHECKPOINT_CONTENT),
                    }
                ),
                options=InMemorySandboxClientOptions(owner_id="installed-package-probe"),
            )
            sessions.append(source)
            await source.start()
            assert (await source.read(Path(_CHECKPOINT_PATH))).read() == _CHECKPOINT_CONTENT

        with _surface("snapshot state serialization and replacement resume"):
            source_handle = _handle(source)
            await source.stop()
            persisted_state = _provider_state(source).model_copy(deep=True)
            state_payload = client.serialize_session_state(persisted_state)
            json_payload = json.dumps(state_payload, sort_keys=True)
            decoded_payload = json.loads(json_payload)
            assert isinstance(decoded_payload, dict)
            assert "service" not in decoded_payload
            assert "session" not in decoded_payload
            restored_state = client.deserialize_session_state(
                cast(dict[str, object], decoded_payload)
            )
            assert restored_state.type == "mem_sandbox"
            assert restored_state.workspace_archive_format_version is not None
            await _retire_session(client, bundle.service, source)
            sessions.remove(source)

            resumed = await client.resume(restored_state)
            sessions.append(resumed)
            await resumed.start()
            replacement_handle = _handle(resumed)
            assert replacement_handle != source_handle
            assert (await resumed.read(Path(_CHECKPOINT_PATH))).read() == _CHECKPOINT_CONTENT
            report["state_type"] = restored_state.type
            report["replacement_resume"] = True

        with _surface("capability binding and Runner tool loop"):
            capability = InMemorySandboxCapability()
            model = DeterministicModel("runner")
            agent = SandboxAgent(
                name="Installed package Runner probe",
                model=model,
                capabilities=[capability],
            )
            result = await Runner.run(
                agent,
                "Write the deterministic result artifact.",
                max_turns=4,
                run_config=RunConfig(
                    tracing_disabled=True,
                    sandbox=SandboxRunConfig(session=resumed),
                ),
            )
            assert result.final_output == "Runner tool loop complete."
            assert capability.session is None
            assert model.tool_names == [_TOOLS, _TOOLS]
            assert (await resumed.read(Path(_RUNNER_RESULT_PATH))).read() == _RUNNER_RESULT_CONTENT
            report["runner_output"] = result.final_output
            report["tools"] = _TOOLS
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await _cleanup_sessions(
                client=client,
                service=bundle.service,
                sessions=sessions,
            )
        except BaseException as cleanup_error:
            if primary is not None:
                primary.add_note(
                    "secondary compatibility surface "
                    f"'SDK session, backend, and service cleanup' failed with "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            else:
                raise CompatibilityProbeError(
                    "OpenAI Agents SDK compatibility surface "
                    "'SDK session, backend, and service cleanup' failed with "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                ) from cleanup_error
    return report


def _document_manifest() -> Manifest:
    return Manifest(
        entries={
            "source": Dir(children={"release-brief.txt": File(content=_BRIEF_CONTENT)}),
            "drafts": Dir(children={"release-notes.md": File(content=_DRAFT_CONTENT)}),
            "review": Dir(children={"instructions.md": File(content=_INSTRUCTIONS_CONTENT)}),
        }
    )


async def _run_document_stage(
    session: OpenAISandboxSession,
    stage: Literal["document-review-editor", "document-review-reviewer"],
) -> tuple[str, DeterministicModel]:
    model = DeterministicModel(stage)
    capability = InMemorySandboxCapability()
    agent = SandboxAgent(
        name=stage,
        instructions=f"Compatibility scenario stage: {stage}",
        model=model,
        capabilities=[capability],
    )
    result = await Runner.run(
        agent,
        f"Complete the installed-package {stage} workflow.",
        max_turns=8,
        run_config=RunConfig(
            tracing_disabled=True,
            sandbox=SandboxRunConfig(session=session),
        ),
    )
    assert isinstance(result.final_output, str)
    assert capability.session is None
    assert all(tool_names == _TOOLS for tool_names in model.tool_names)
    return result.final_output, model


async def _exercise_document_review() -> list[str]:
    bundle = _create_service_bundle(DocumentReviewPolicyEngine())
    client = InMemorySandboxClient(bundle.service)
    sessions: list[OpenAISandboxSession] = []
    primary: BaseException | None = None
    try:
        with _surface("document-review editor and reviewer workflow"):
            session = await client.create(
                manifest=_document_manifest(),
                options=InMemorySandboxClientOptions(owner_id="installed-package-document-review"),
            )
            sessions.append(session)
            editor_output, editor_model = await _run_document_stage(
                session,
                "document-review-editor",
            )
            reviewer_output, reviewer_model = await _run_document_stage(
                session,
                "document-review-reviewer",
            )
            assert editor_model.calls == 6
            assert reviewer_model.calls == 6
            assert (await session.read(Path(_BRIEF_PATH))).read() == _BRIEF_CONTENT
            assert (await session.read(Path(_INSTRUCTIONS_PATH))).read() == _INSTRUCTIONS_CONTENT
            assert (await session.read(Path(_DRAFT_PATH))).read() == _EXPECTED_DRAFT_CONTENT
            assert (await session.read(Path(_REVIEW_PATH))).read() == _REVIEW_CONTENT
            return [editor_output, reviewer_output]
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await _cleanup_sessions(
                client=client,
                service=bundle.service,
                sessions=sessions,
            )
        except BaseException as cleanup_error:
            if primary is not None:
                primary.add_note(
                    "secondary compatibility surface "
                    f"'SDK session, backend, and service cleanup' failed with "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            else:
                raise CompatibilityProbeError(
                    "OpenAI Agents SDK compatibility surface "
                    "'SDK session, backend, and service cleanup' failed with "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                ) from cleanup_error


def _legacy_namespace_available() -> bool:
    try:
        import mem_sandbox.integrations.openai_agents  # pyright: ignore[reportMissingImports] # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _repository_samples_available() -> bool:
    try:
        return importlib.util.find_spec("samples.openai_agents_sdk") is not None
    except ModuleNotFoundError:
        return False


async def _run_probe(
    *,
    expected_core_version: str,
    expected_adapter_version: str,
    expected_sdk_version: str,
) -> dict[str, object]:
    with _surface("distribution versions"):
        distributions = {
            "mem-sandbox": importlib.metadata.version("mem-sandbox"),
            "mem-sandbox-openai-agents": importlib.metadata.version("mem-sandbox-openai-agents"),
            "openai-agents": importlib.metadata.version("openai-agents"),
        }
        assert distributions == {
            "mem-sandbox": expected_core_version,
            "mem-sandbox-openai-agents": expected_adapter_version,
            "openai-agents": expected_sdk_version,
        }

    with _surface("public imports and namespace ownership"):
        assert sys.flags.isolated == 1
        assert adapter.InMemorySandboxClient is InMemorySandboxClient
        assert adapter.InMemorySandboxClient.__module__ == "mem_sandbox_openai_agents.adapter"
        repository_samples_available = _repository_samples_available()
        legacy_namespace_available = _legacy_namespace_available()
        assert repository_samples_available is False
        assert legacy_namespace_available is False

    sdk_contract = await _exercise_lifecycle_and_runner()
    sdk_contract["document_review_outputs"] = await _exercise_document_review()

    return {
        "distributions": distributions,
        "isolated": True,
        "namespaces": {
            "adapter": adapter.InMemorySandboxClient.__module__,
            "legacy_namespace_available": legacy_namespace_available,
            "repository_samples_available": repository_samples_available,
        },
        "sdk_contract": sdk_contract,
        "surfaces": _SURFACES,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-core-version", required=True)
    parser.add_argument("--expected-adapter-version", required=True)
    parser.add_argument("--expected-sdk-version", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        _run_probe(
            expected_core_version=cast(str, args.expected_core_version),
            expected_adapter_version=cast(str, args.expected_adapter_version),
            expected_sdk_version=cast(str, args.expected_sdk_version),
        )
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
