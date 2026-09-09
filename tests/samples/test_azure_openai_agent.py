from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from io import StringIO
from typing import Any, cast

import pytest
from agents import ModelResponse, Usage
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.tool import Tool
from openai import AsyncAzureOpenAI
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from samples.azure_openai_agent.__main__ import build_parser, main
from samples.azure_openai_agent.app import (
    InspectionContext,
    create_azure_model,
    run_scenario,
)
from samples.azure_openai_agent.cli import run_inspection_cli
from samples.azure_openai_agent.config import AzureOpenAISettings
from samples.azure_openai_agent.scenarios import get_scenario, list_scenarios
from samples.azure_openai_agent.scenarios.config_migration import (
    API_PATH,
    WORKER_PATH,
)
from samples.azure_openai_agent.scenarios.config_migration import (
    REPORT_CONTENT as MIGRATION_REPORT,
)
from samples.azure_openai_agent.scenarios.config_migration import (
    REPORT_PATH as MIGRATION_REPORT_PATH,
)
from samples.azure_openai_agent.scenarios.data_pipeline import OUTPUT_PATH as PIPELINE_PATH
from samples.azure_openai_agent.scenarios.incident_triage import (
    REPORT_CONTENT as INCIDENT_REPORT,
)
from samples.azure_openai_agent.scenarios.incident_triage import (
    REPORT_PATH as INCIDENT_REPORT_PATH,
)
from samples.azure_openai_agent.scenarios.multi_agent_handoff import (
    CONFIG_PATH,
    PLAN_CONTENT,
    PLAN_PATH,
    REVIEW_CONTENT,
    REVIEW_PATH,
)
from samples.azure_openai_agent.scenarios.policy_recovery import (
    REPORT_CONTENT as POLICY_REPORT,
)
from samples.azure_openai_agent.scenarios.policy_recovery import (
    REPORT_PATH as POLICY_REPORT_PATH,
)
from samples.azure_openai_agent.scenarios.quota_recovery import (
    OUTPUT_CONTENT as QUOTA_CONTENT,
)
from samples.azure_openai_agent.scenarios.quota_recovery import (
    OUTPUT_PATH as QUOTA_PATH,
)
from samples.azure_openai_agent.scenarios.snapshot_branching import (
    CHOICE_PATH,
)
from samples.azure_openai_agent.scenarios.snapshot_branching import (
    PLAN_PATH as BRANCH_PLAN_PATH,
)
from samples.azure_openai_agent.scenarios.workspace_edit import (
    SAMPLE_PATH,
)
from samples.azure_openai_agent.service import (
    create_sample_service,
    create_sample_service_bundle,
)

from mem_sandbox.service import (
    CreateSandboxRequest,
    InMemorySandboxService,
    ResumeSandboxRequest,
    SandboxHandle,
    SandboxNotFound,
)
from mem_sandbox.session import SandboxSession

_EXPECTED_TOOLS = ["execute", "read_file", "write_file", "apply_patch"]
_SCENARIO_NAMES = [
    "workspace-edit",
    "incident-triage",
    "config-migration",
    "data-pipeline",
    "policy-recovery",
    "quota-recovery",
    "multi-agent-handoff",
    "snapshot-branching",
]


class RecordingService:
    def __init__(
        self,
        delegate: InMemorySandboxService,
        *,
        delete_failure: BaseException | None = None,
    ) -> None:
        self.delegate = delegate
        self.delete_failure = delete_failure
        self.created_handles: list[SandboxHandle] = []
        self.deleted_handles: list[SandboxHandle] = []

    async def create(self, request: CreateSandboxRequest) -> SandboxHandle:
        handle = await self.delegate.create(request)
        self.created_handles.append(handle)
        return handle

    async def get_session(self, handle: SandboxHandle) -> SandboxSession:
        return await self.delegate.get_session(handle)

    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle:
        handle = await self.delegate.resume(request)
        self.created_handles.append(handle)
        return handle

    async def delete(self, handle: SandboxHandle) -> None:
        self.deleted_handles.append(handle)
        await self.delegate.delete(handle)
        if self.delete_failure is not None:
            raise self.delete_failure

    async def close(self) -> None:
        await self.delegate.close()


class DeterministicScenarioModel(Model):
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: dict[str, int] = {}
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
            model_settings,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        if self.failure is not None:
            raise self.failure
        stage_key = _stage_key(system_instructions)
        call = self.calls.get(stage_key, 0) + 1
        self.calls[stage_key] = call
        self.tool_names.append([tool.name for tool in tools])
        output = _scripted_output(stage_key, call, input)
        return ModelResponse(
            output=cast(Any, output),
            usage=Usage(),
            response_id=f"{stage_key}_{call}",
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
    stage: str,
    call: int,
    input: str | list[TResponseInputItem],
) -> list[ResponseFunctionToolCall | ResponseOutputMessage]:
    if stage == "workspace-edit":
        if call == 1:
            return [_write("write_pending", SAMPLE_PATH, "status=pending\n")]
        if call == 2:
            return [_read("read_pending", SAMPLE_PATH)]
        if call == 3:
            return [
                _patch(
                    "patch_complete",
                    SAMPLE_PATH,
                    "status=pending",
                    "status=complete",
                    _latest_hash(input),
                )
            ]
        if call == 4:
            return [_read("read_complete", SAMPLE_PATH)]
        return [_message("Workspace edit complete.")]

    if stage == "incident-triage":
        if call == 1:
            return [
                _execute(
                    "search_logs",
                    "mkdir -p /workspace/reports && grep -r timeout /workspace/logs | wc -l",
                )
            ]
        if call == 2:
            return [
                _write(
                    "write_incident",
                    INCIDENT_REPORT_PATH,
                    INCIDENT_REPORT.decode(),
                )
            ]
        return [_message("Incident triage complete.")]

    if stage == "config-migration":
        if call == 1:
            return [_read("read_api", API_PATH)]
        if call == 2:
            return [_read("read_worker", WORKER_PATH)]
        if call == 3:
            hashes = _successful_results(input)[-2:]
            return [
                _tool_call(
                    call_id="patch_configs",
                    name="apply_patch",
                    arguments={
                        "patch": (
                            f"--- {API_PATH}\n"
                            f"+++ {API_PATH}\n"
                            "@@ -1,2 +1,2 @@\n"
                            "-timeout_seconds=15\n"
                            "+timeout_seconds=30\n"
                            " cache=false\n"
                            f"--- {WORKER_PATH}\n"
                            f"+++ {WORKER_PATH}\n"
                            "@@ -1,2 +1,2 @@\n"
                            "-timeout_seconds=15\n"
                            "+timeout_seconds=30\n"
                            " cache=false\n"
                        ),
                        "expected_hashes": [
                            {
                                "path": API_PATH,
                                "content_hash": hashes[0]["content_hash"],
                            },
                            {
                                "path": WORKER_PATH,
                                "content_hash": hashes[1]["content_hash"],
                            },
                        ],
                    },
                )
            ]
        if call == 4:
            return [
                _write(
                    "write_migration_report",
                    MIGRATION_REPORT_PATH,
                    MIGRATION_REPORT.decode(),
                )
            ]
        return [_message("Configuration migration complete.")]

    if stage == "data-pipeline":
        if call == 1:
            return [
                _execute(
                    "run_pipeline",
                    "mkdir -p /workspace/output && "
                    f"sort /workspace/data/events.txt | uniq -c > {PIPELINE_PATH}",
                )
            ]
        if call == 2:
            return [_read("read_counts", PIPELINE_PATH)]
        return [_message("Data pipeline complete.")]

    if stage == "policy-recovery":
        if call == 1:
            return [_execute("denied_cat", "cat /workspace/evidence/audit.log")]
        if call == 2:
            assert _latest_tool_output(input)["ok"] is False
            return [_read("read_evidence", "/workspace/evidence/audit.log")]
        if call == 3:
            return [
                _write(
                    "write_policy_report",
                    POLICY_REPORT_PATH,
                    POLICY_REPORT.decode(),
                )
            ]
        return [_message("Policy-aware recovery complete.")]

    if stage == "quota-recovery":
        if call == 1:
            return [_write("oversized_write", QUOTA_PATH, "x" * 100)]
        if call == 2:
            assert _latest_tool_output(input)["ok"] is False
            return [_write("compact_write", QUOTA_PATH, QUOTA_CONTENT.decode())]
        if call == 3:
            return [_read("read_compact", QUOTA_PATH)]
        return [_message("Quota recovery complete.")]

    if stage == "multi-agent-plan":
        if call == 1:
            return [_write("write_plan", PLAN_PATH, PLAN_CONTENT.decode())]
        return [_message("Plan handed off.")]

    if stage == "multi-agent-implement":
        if call == 1:
            return [_read("read_config", CONFIG_PATH)]
        if call == 2:
            return [
                _patch(
                    "enable_cache",
                    CONFIG_PATH,
                    "cache=false",
                    "cache=true",
                    _latest_hash(input),
                )
            ]
        return [_message("Implementation complete.")]

    if stage == "multi-agent-review":
        if call == 1:
            return [_read("review_config", CONFIG_PATH)]
        if call == 2:
            return [_write("write_review", REVIEW_PATH, REVIEW_CONTENT.decode())]
        return [_message("Review approved.")]

    if stage == "snapshot-baseline":
        if call == 1:
            return [_read("read_branch_plan", BRANCH_PLAN_PATH)]
        assert _latest_tool_output(input)["ok"] is True
        return [_message("Branch baseline prepared.")]

    if stage in {"snapshot-conservative", "snapshot-aggressive"}:
        selected = stage.removeprefix("snapshot-")
        if call == 1:
            return [_read(f"read_{selected}", CHOICE_PATH)]
        if call == 2:
            return [
                _patch(
                    f"choose_{selected}",
                    CHOICE_PATH,
                    "choice=baseline",
                    f"choice={selected}",
                    _latest_hash(input),
                )
            ]
        return [_message(f"{selected.title()} branch complete.")]

    raise AssertionError(f"no deterministic script for {stage}")


def _stage_key(system_instructions: str | None) -> str:
    assert system_instructions is not None
    marker = "Scenario stage key: "
    assert marker in system_instructions
    return system_instructions.rsplit(marker, 1)[1].splitlines()[0]


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


def _execute(call_id: str, command: str) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id=call_id,
        name="execute",
        arguments={"command": command},
    )


def _patch(
    call_id: str,
    path: str,
    old: str,
    new: str,
    content_hash: str,
) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id=call_id,
        name="apply_patch",
        arguments={
            "patch": (f"--- {path}\n+++ {path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"),
            "expected_hashes": [{"path": path, "content_hash": content_hash}],
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
    result = _successful_results(input)[-1]
    return cast(str, result["content_hash"])


def _successful_results(
    input: str | list[TResponseInputItem],
) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], output["result"])
        for output in _tool_outputs(input)
        if output["ok"] is True
    ]


def _latest_tool_output(
    input: str | list[TResponseInputItem],
) -> dict[str, Any]:
    return _tool_outputs(input)[-1]


def _tool_outputs(
    input: str | list[TResponseInputItem],
) -> list[dict[str, Any]]:
    assert isinstance(input, list)
    outputs: list[dict[str, Any]] = []
    for item in input:
        raw_item = cast(dict[str, Any], item)
        if raw_item.get("type") != "function_call_output":
            continue
        outputs.append(json.loads(cast(str, raw_item["output"])))
    return outputs


def _environment() -> dict[str, str]:
    return {
        "AZURE_OPENAI_ENDPOINT": "https://sample-resource.openai.azure.com/",
        "AZURE_OPENAI_API_KEY": "sample-secret-key",
        "AZURE_OPENAI_API_VERSION": "2025-01-01-preview",
        "AZURE_OPENAI_DEPLOYMENT": "sample-deployment",
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://sample-resource.openai.azure.com/",
        "https://sample-deployment.cognitiveservices.azure.com/",
    ],
)
def test_settings_load_supported_endpoint_and_hide_api_key(endpoint: str) -> None:
    environment = _environment()
    environment["AZURE_OPENAI_ENDPOINT"] = endpoint

    settings = AzureOpenAISettings.from_environment(environment)

    assert settings.endpoint == endpoint.rstrip("/")
    assert settings.api_version == "2025-01-01-preview"
    assert settings.deployment == "sample-deployment"
    assert settings.api_key == "sample-secret-key"
    assert settings.api_key not in repr(settings)


@pytest.mark.parametrize(
    "variable",
    [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_DEPLOYMENT",
    ],
)
def test_settings_reject_missing_or_empty_values(variable: str) -> None:
    missing = _environment()
    missing.pop(variable)
    with pytest.raises(ValueError, match=variable):
        AzureOpenAISettings.from_environment(missing)

    empty = _environment()
    empty[variable] = "   "
    with pytest.raises(ValueError, match=variable):
        AzureOpenAISettings.from_environment(empty)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://sample.services.ai.azure.com",
        "https://sample.models.ai.azure.com",
        "https://api.openai.com",
        "http://sample-resource.openai.azure.com",
        "https://user:password@sample.openai.azure.com",
        "https://sample.openai.azure.com/openai",
    ],
)
def test_settings_reject_unsupported_endpoint(endpoint: str) -> None:
    environment = _environment()
    environment["AZURE_OPENAI_ENDPOINT"] = endpoint

    with pytest.raises(ValueError, match="Azure OpenAI resource endpoint"):
        AzureOpenAISettings.from_environment(environment)


@pytest.mark.asyncio
async def test_azure_model_composition_requires_no_network() -> None:
    settings = AzureOpenAISettings.from_environment(_environment())

    client, model = create_azure_model(settings)
    try:
        assert isinstance(client, AsyncAzureOpenAI)
        assert isinstance(model, OpenAIChatCompletionsModel)
    finally:
        await client.close()


def test_registry_and_parser_expose_all_scenarios() -> None:
    assert [scenario.name for scenario in list_scenarios()] == _SCENARIO_NAMES
    assert build_parser().parse_args([]).scenario == "workspace-edit"
    args = build_parser().parse_args(
        [
            "--scenario",
            "incident-triage",
            "--inspect",
            "--inspect-on-failure",
        ]
    )
    assert args.scenario == "incident-triage"
    assert args.inspect is True
    assert args.inspect_on_failure is True


@pytest.mark.asyncio
async def test_list_scenarios_does_not_require_azure_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await main(["--list-scenarios"])

    output = capsys.readouterr().out
    for name in _SCENARIO_NAMES:
        assert f"{name}:" in output


@pytest.mark.parametrize("scenario_name", _SCENARIO_NAMES)
@pytest.mark.asyncio
async def test_registered_scenario_runs_without_network_and_cleans_backends(
    scenario_name: str,
) -> None:
    scenario = get_scenario(scenario_name)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel()
    try:
        result = await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
            snapshot_store=bundle.snapshot_store,
            clock=bundle.clock,
        )

        assert result.scenario_name == scenario_name
        assert result.artifacts
        assert all(names == _EXPECTED_TOOLS for names in model.tool_names)
        if scenario_name == "snapshot-branching":
            assert result.selected_branch == "aggressive"
            assert len(service.created_handles) == 3
        else:
            assert result.selected_branch is None
            assert len(service.created_handles) == 1
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_inspect_runs_on_success_against_same_session() -> None:
    scenario = get_scenario("workspace-edit")
    delegate = create_sample_service()
    service = RecordingService(delegate)
    model = DeterministicScenarioModel()
    cli_output = StringIO()
    inspected: list[InspectionContext] = []

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        inspected.append(context)
        await run_inspection_cli(
            session,
            input_stream=StringIO(f"cat {SAMPLE_PATH}\nexit\n"),
            output_stream=cli_output,
            error_stream=StringIO(),
        )

    try:
        result = await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
            inspector=inspect,
            inspect_success=True,
        )

        assert inspected == [
            InspectionContext(
                scenario_name="workspace-edit",
                result=result,
                error=None,
            )
        ]
        assert "status=complete\n" in cli_output.getvalue()
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_inspect_on_failure_runs_before_cleanup_and_preserves_error() -> None:
    scenario = get_scenario("workspace-edit")
    delegate = create_sample_service()
    service = RecordingService(delegate)
    model = DeterministicScenarioModel(failure=RuntimeError("model failed"))
    inspected_errors: list[BaseException] = []

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert context.result is None
        assert context.error is not None
        inspected_errors.append(context.error)
        await run_inspection_cli(
            session,
            input_stream=StringIO("pwd\nexit\n"),
            output_stream=StringIO(),
            error_stream=StringIO(),
        )

    try:
        with pytest.raises(RuntimeError, match="model failed") as captured:
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
            )

        assert inspected_errors == [captured.value]
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_snapshot_failure_inspects_baseline_before_cleanup() -> None:
    scenario = get_scenario("snapshot-branching")
    bundle = create_sample_service_bundle()
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(failure=RuntimeError("branch setup failed"))
    inspected = False

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        nonlocal inspected
        assert context.error is not None
        inspected = True
        await run_inspection_cli(
            session,
            input_stream=StringIO(f"cat {CHOICE_PATH}\nexit\n"),
            output_stream=StringIO(),
            error_stream=StringIO(),
        )

    try:
        with pytest.raises(RuntimeError, match="branch setup failed"):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
                snapshot_store=bundle.snapshot_store,
                clock=bundle.clock,
            )

        assert inspected is True
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_snapshot_success_inspects_selected_branch() -> None:
    scenario = get_scenario("snapshot-branching")
    bundle = create_sample_service_bundle()
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel()
    cli_output = StringIO()

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert context.result is not None
        assert context.result.selected_branch == "aggressive"
        await run_inspection_cli(
            session,
            input_stream=StringIO(f"cat {CHOICE_PATH}\nexit\n"),
            output_stream=cli_output,
            error_stream=StringIO(),
        )

    try:
        await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
            inspector=inspect,
            inspect_success=True,
            snapshot_store=bundle.snapshot_store,
            clock=bundle.clock,
        )

        assert "choice=aggressive\n" in cli_output.getvalue()
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_policy_recovery_allows_post_run_inspection_commands() -> None:
    scenario = get_scenario("policy-recovery")
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel()
    cli_output = StringIO()

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert context.result is not None
        await run_inspection_cli(
            session,
            input_stream=StringIO("cat /workspace/evidence/audit.log\nexit\n"),
            output_stream=cli_output,
            error_stream=StringIO(),
        )

    try:
        await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
            inspector=inspect,
            inspect_success=True,
            snapshot_store=bundle.snapshot_store,
            clock=bundle.clock,
        )

        assert "event=credential-probe\nstatus=contained\n" in cli_output.getvalue()
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_failure_does_not_inspect_without_requested_option() -> None:
    scenario = get_scenario("workspace-edit")
    delegate = create_sample_service()
    service = RecordingService(delegate)
    model = DeterministicScenarioModel(failure=asyncio.CancelledError())
    inspected = False

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        nonlocal inspected
        _ = (context, session)
        inspected = True

    try:
        with pytest.raises(asyncio.CancelledError):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
            )

        assert inspected is False
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_model_failure_remains_primary_when_delete_also_fails() -> None:
    scenario = get_scenario("workspace-edit")
    delegate = create_sample_service()
    service = RecordingService(
        delegate,
        delete_failure=RuntimeError("delete failed"),
    )
    model = DeterministicScenarioModel(failure=RuntimeError("model failed"))
    try:
        with pytest.raises(RuntimeError, match="model failed") as captured:
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
            )

        assert captured.value.__notes__ == ["secondary backend delete failure: delete failed"]
        await _assert_backends_deleted(service)
    finally:
        await service.close()


async def _assert_backends_deleted(service: RecordingService) -> None:
    assert set(service.deleted_handles) == set(service.created_handles)
    for handle in service.created_handles:
        with pytest.raises(SandboxNotFound):
            await service.delegate.get_session(handle)
