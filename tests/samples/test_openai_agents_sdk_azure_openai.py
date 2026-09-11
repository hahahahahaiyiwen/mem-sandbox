"""Azure provider and shared behavior tests for the OpenAI Agents SDK samples."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from io import StringIO
from typing import Any, Literal, cast

import pytest
from agents import ModelResponse, Usage
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.sandbox.entries import File
from agents.sandbox.errors import SnapshotNotRestorableError
from agents.tool import Tool
from openai import AsyncAzureOpenAI
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_prompt_param import ResponsePromptParam
from samples.openai_agents_sdk.application import build_sample_parser, run_provider_sample
from samples.openai_agents_sdk.providers.azure_openai.__main__ import build_parser, main
from samples.openai_agents_sdk.providers.azure_openai.config import AzureOpenAISettings
from samples.openai_agents_sdk.providers.azure_openai.model import create_azure_model
from samples.openai_agents_sdk.runner import (
    InspectionContext,
    ScenarioVerificationError,
    run_scenario,
)
from samples.openai_agents_sdk.scenarios import (
    PauseContinueScenario,
    SnapshotBranchingScenario,
    StagedScenario,
    get_scenario,
    list_scenarios,
)
from samples.openai_agents_sdk.scenarios.config_migration import (
    API_PATH,
    MIGRATED_CONFIG,
    WORKER_PATH,
)
from samples.openai_agents_sdk.scenarios.config_migration import (
    REPORT_CONTENT as MIGRATION_REPORT,
)
from samples.openai_agents_sdk.scenarios.config_migration import (
    REPORT_PATH as MIGRATION_REPORT_PATH,
)
from samples.openai_agents_sdk.scenarios.data_pipeline import (
    OUTPUT_CONTENT as PIPELINE_CONTENT,
)
from samples.openai_agents_sdk.scenarios.data_pipeline import (
    OUTPUT_PATH as PIPELINE_PATH,
)
from samples.openai_agents_sdk.scenarios.document_review import (
    BRIEF_CONTENT,
    BRIEF_PATH,
    DRAFT_CONTENT,
    DRAFT_PATH,
    EXPECTED_DRAFT_CONTENT,
    INSTRUCTIONS_CONTENT,
    INSTRUCTIONS_PATH,
)
from samples.openai_agents_sdk.scenarios.document_review import (
    REVIEW_CONTENT as DOCUMENT_REVIEW_CONTENT,
)
from samples.openai_agents_sdk.scenarios.document_review import (
    REVIEW_PATH as DOCUMENT_REVIEW_PATH,
)
from samples.openai_agents_sdk.scenarios.incident_triage import (
    REPORT_CONTENT as INCIDENT_REPORT,
)
from samples.openai_agents_sdk.scenarios.incident_triage import (
    REPORT_PATH as INCIDENT_REPORT_PATH,
)
from samples.openai_agents_sdk.scenarios.independent_reviewers import (
    BASELINE_REVIEW_STATUS,
    CLARITY_REVIEW_CONTENT,
    CLARITY_REVIEW_STATUS,
    REVIEW_CRITERIA_CONTENT,
    REVIEW_CRITERIA_PATH,
    REVIEW_FINDINGS_PATH,
    REVIEW_SOURCE_CONTENT,
    REVIEW_SOURCE_PATH,
    REVIEW_STATUS_PATH,
    RISK_REVIEW_CONTENT,
    RISK_REVIEW_STATUS,
)
from samples.openai_agents_sdk.scenarios.multi_agent_handoff import (
    CONFIG_CONTENT,
    CONFIG_PATH,
    PLAN_CONTENT,
    PLAN_PATH,
    REVIEW_CONTENT,
    REVIEW_PATH,
)
from samples.openai_agents_sdk.scenarios.pause_continue import (
    CHECKPOINT_CONTENT,
    CHECKPOINT_PATH,
    COMPLETED_STATUS,
    CONTINUATION_CONTENT,
    CONTINUATION_PATH,
    PAUSED_STATUS,
    REQUEST_CONTENT,
    REQUEST_PATH,
    STATUS_PATH,
)
from samples.openai_agents_sdk.scenarios.policy_recovery import (
    EVIDENCE_CONTENT,
    EVIDENCE_PATH,
)
from samples.openai_agents_sdk.scenarios.policy_recovery import (
    REPORT_CONTENT as POLICY_REPORT,
)
from samples.openai_agents_sdk.scenarios.policy_recovery import (
    REPORT_PATH as POLICY_REPORT_PATH,
)
from samples.openai_agents_sdk.scenarios.quota_recovery import (
    OUTPUT_CONTENT as QUOTA_CONTENT,
)
from samples.openai_agents_sdk.scenarios.quota_recovery import (
    OUTPUT_PATH as QUOTA_PATH,
)
from samples.openai_agents_sdk.scenarios.snapshot_branching import (
    AGGRESSIVE_CONTENT,
    CHOICE_PATH,
    CONSERVATIVE_CONTENT,
)
from samples.openai_agents_sdk.scenarios.snapshot_branching import (
    CHECKPOINT_CONTENT as BRANCH_CHECKPOINT_CONTENT,
)
from samples.openai_agents_sdk.scenarios.snapshot_branching import (
    CHECKPOINT_PATH as BRANCH_CHECKPOINT_PATH,
)
from samples.openai_agents_sdk.scenarios.snapshot_branching import (
    PLAN_CONTENT as BRANCH_PLAN_CONTENT,
)
from samples.openai_agents_sdk.scenarios.snapshot_branching import (
    PLAN_PATH as BRANCH_PLAN_PATH,
)
from samples.openai_agents_sdk.scenarios.workspace_edit import (
    EXPECTED_CONTENT as WORKSPACE_CONTENT,
)
from samples.openai_agents_sdk.scenarios.workspace_edit import (
    SAMPLE_PATH,
)
from samples.shared.cli import run_inspection_cli
from samples.shared.service import (
    create_sample_service,
    create_sample_service_bundle,
)

from mem_sandbox.service import (
    CreateSandboxRequest,
    FactorySnapshotStore,
    InMemorySandboxService,
    ResumeSandboxRequest,
    SandboxHandle,
    SandboxNotFound,
)
from mem_sandbox.session import ReadBytesRequest, SandboxSession
from mem_sandbox.snapshots import (
    SandboxSnapshot,
    SandboxSnapshotDraft,
    SnapshotNotFound,
    SnapshotRef,
)

_EXPECTED_TOOLS = ["execute", "read_file", "write_file", "apply_patch"]
_INVALID_CHECKPOINT_CONTENT = b"# Invalid checkpoint\n"
_SCENARIO_NAMES = [
    "workspace-edit",
    "document-review",
    "independent-reviewers",
    "pause-continue",
    "incident-triage",
    "config-migration",
    "data-pipeline",
    "policy-recovery",
    "quota-recovery",
    "multi-agent-handoff",
    "snapshot-branching",
]


type ArtifactEvidence = tuple[str, bytes]


@dataclass(frozen=True, slots=True)
class ExpectedBranchEvidence:
    name: str
    artifacts: tuple[ArtifactEvidence, ...]


@dataclass(frozen=True, slots=True)
class ExpectedScenarioEvidence:
    selected_artifacts: tuple[ArtifactEvidence, ...]
    stage_order: tuple[str, ...]
    branches: tuple[ExpectedBranchEvidence, ...] = ()
    baseline_artifacts: tuple[ArtifactEvidence, ...] = ()
    selected_branch: str | None = None


_EXPECTED_SCENARIO_EVIDENCE: dict[str, ExpectedScenarioEvidence] = {
    "workspace-edit": ExpectedScenarioEvidence(
        selected_artifacts=((SAMPLE_PATH, WORKSPACE_CONTENT),),
        stage_order=("workspace-edit",),
    ),
    "document-review": ExpectedScenarioEvidence(
        selected_artifacts=(
            (BRIEF_PATH, BRIEF_CONTENT),
            (INSTRUCTIONS_PATH, INSTRUCTIONS_CONTENT),
            (DRAFT_PATH, EXPECTED_DRAFT_CONTENT),
            (DOCUMENT_REVIEW_PATH, DOCUMENT_REVIEW_CONTENT),
        ),
        stage_order=("document-review-editor", "document-review-reviewer"),
    ),
    "independent-reviewers": ExpectedScenarioEvidence(
        selected_artifacts=(
            (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
            (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
            (REVIEW_STATUS_PATH, RISK_REVIEW_STATUS),
            (REVIEW_FINDINGS_PATH, RISK_REVIEW_CONTENT),
        ),
        stage_order=(
            "independent-reviewers-baseline",
            "independent-reviewer-risk",
            "independent-reviewer-clarity",
        ),
        branches=(
            ExpectedBranchEvidence(
                name="risk",
                artifacts=(
                    (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
                    (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
                    (REVIEW_STATUS_PATH, RISK_REVIEW_STATUS),
                    (REVIEW_FINDINGS_PATH, RISK_REVIEW_CONTENT),
                ),
            ),
            ExpectedBranchEvidence(
                name="clarity",
                artifacts=(
                    (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
                    (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
                    (REVIEW_STATUS_PATH, CLARITY_REVIEW_STATUS),
                    (REVIEW_FINDINGS_PATH, CLARITY_REVIEW_CONTENT),
                ),
            ),
        ),
        baseline_artifacts=(
            (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
            (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
            (REVIEW_STATUS_PATH, BASELINE_REVIEW_STATUS),
        ),
        selected_branch="risk",
    ),
    "pause-continue": ExpectedScenarioEvidence(
        selected_artifacts=(
            (REQUEST_PATH, REQUEST_CONTENT),
            (STATUS_PATH, COMPLETED_STATUS),
            (CHECKPOINT_PATH, CHECKPOINT_CONTENT),
            (CONTINUATION_PATH, CONTINUATION_CONTENT),
        ),
        stage_order=("pause-continue-initial", "pause-continue-resumed"),
    ),
    "incident-triage": ExpectedScenarioEvidence(
        selected_artifacts=((INCIDENT_REPORT_PATH, INCIDENT_REPORT),),
        stage_order=("incident-triage",),
    ),
    "config-migration": ExpectedScenarioEvidence(
        selected_artifacts=(
            (API_PATH, MIGRATED_CONFIG),
            (WORKER_PATH, MIGRATED_CONFIG),
            (MIGRATION_REPORT_PATH, MIGRATION_REPORT),
        ),
        stage_order=("config-migration",),
    ),
    "data-pipeline": ExpectedScenarioEvidence(
        selected_artifacts=((PIPELINE_PATH, PIPELINE_CONTENT),),
        stage_order=("data-pipeline",),
    ),
    "policy-recovery": ExpectedScenarioEvidence(
        selected_artifacts=(
            (EVIDENCE_PATH, EVIDENCE_CONTENT),
            (POLICY_REPORT_PATH, POLICY_REPORT),
        ),
        stage_order=("policy-recovery",),
    ),
    "quota-recovery": ExpectedScenarioEvidence(
        selected_artifacts=((QUOTA_PATH, QUOTA_CONTENT),),
        stage_order=("quota-recovery",),
    ),
    "multi-agent-handoff": ExpectedScenarioEvidence(
        selected_artifacts=(
            (CONFIG_PATH, CONFIG_CONTENT),
            (PLAN_PATH, PLAN_CONTENT),
            (REVIEW_PATH, REVIEW_CONTENT),
        ),
        stage_order=(
            "multi-agent-plan",
            "multi-agent-implement",
            "multi-agent-review",
        ),
    ),
    "snapshot-branching": ExpectedScenarioEvidence(
        selected_artifacts=(
            (CHOICE_PATH, AGGRESSIVE_CONTENT),
            (BRANCH_PLAN_PATH, BRANCH_PLAN_CONTENT),
            (BRANCH_CHECKPOINT_PATH, BRANCH_CHECKPOINT_CONTENT),
        ),
        stage_order=(
            "snapshot-baseline",
            "snapshot-conservative",
            "snapshot-aggressive",
        ),
        branches=(
            ExpectedBranchEvidence(
                name="conservative",
                artifacts=(
                    (CHOICE_PATH, CONSERVATIVE_CONTENT),
                    (BRANCH_PLAN_PATH, BRANCH_PLAN_CONTENT),
                    (BRANCH_CHECKPOINT_PATH, BRANCH_CHECKPOINT_CONTENT),
                ),
            ),
            ExpectedBranchEvidence(
                name="aggressive",
                artifacts=(
                    (CHOICE_PATH, AGGRESSIVE_CONTENT),
                    (BRANCH_PLAN_PATH, BRANCH_PLAN_CONTENT),
                    (BRANCH_CHECKPOINT_PATH, BRANCH_CHECKPOINT_CONTENT),
                ),
            ),
        ),
        baseline_artifacts=(),
        selected_branch="aggressive",
    ),
}


@dataclass(frozen=True, slots=True)
class ReplacementResumeCancellationState:
    created_handles: tuple[SandboxHandle, ...]
    deleted_handles: tuple[SandboxHandle, ...]
    source_backend_missing: bool


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


class RecordingSnapshotStore:
    def __init__(self, delegate: FactorySnapshotStore) -> None:
        self.delegate = delegate
        self.save_calls = 0
        self.load_calls = 0

    @property
    def process_local(self) -> bool:
        return self.delegate.process_local

    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef:
        self.save_calls += 1
        return await self.delegate.save(draft)

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        self.load_calls += 1
        return await self.delegate.load(snapshot_ref)


class MissingSnapshotStore(RecordingSnapshotStore):
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        self.load_calls += 1
        raise SnapshotNotFound(f"snapshot {snapshot_ref.snapshot_id} is unavailable")


class CancelOnReplacementResumeLoadSnapshotStore(RecordingSnapshotStore):
    def __init__(
        self,
        delegate: FactorySnapshotStore,
        service: RecordingService,
    ) -> None:
        super().__init__(delegate)
        self.service = service
        self.state_at_cancellation: ReplacementResumeCancellationState | None = None

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        self.load_calls += 1
        created_handles = tuple(self.service.created_handles)
        deleted_handles = tuple(self.service.deleted_handles)
        if len(created_handles) == 1 and created_handles[0] in deleted_handles:
            source_handle = created_handles[0]
            try:
                await self.service.delegate.get_session(source_handle)
            except SandboxNotFound:
                self.state_at_cancellation = ReplacementResumeCancellationState(
                    created_handles=created_handles,
                    deleted_handles=deleted_handles,
                    source_backend_missing=True,
                )
                raise asyncio.CancelledError() from None
        return await self.delegate.load(snapshot_ref)


class RecordingModelClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class DeterministicScenarioModel(Model):
    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        failure_stage: str | None = None,
        document_review_boundary: Literal["protected-write", "stale-hash"] | None = None,
        pause_continue_boundary: Literal["invalid-checkpoint"] | None = None,
    ) -> None:
        self.failure = failure
        self.failure_stage = failure_stage
        self.document_review_boundary: Literal["protected-write", "stale-hash"] | None = (
            document_review_boundary
        )
        self.pause_continue_boundary: Literal["invalid-checkpoint"] | None = pause_continue_boundary
        self.boundary_output: dict[str, Any] | None = None
        self.failed_tool_outputs: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, int] = {}
        self.initial_inputs: dict[str, str | list[TResponseInputItem]] = {}
        self.stage_order: list[str] = []
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
        stage_key = _stage_key(system_instructions)
        call = self.calls.get(stage_key, 0) + 1
        self.calls[stage_key] = call
        if call == 1:
            self.initial_inputs[stage_key] = input
            self.stage_order.append(stage_key)
        if self.failure is not None and (
            self.failure_stage is None or self.failure_stage == stage_key
        ):
            raise self.failure
        if stage_key == "document-review-editor" and (
            (self.document_review_boundary == "protected-write" and call == 2)
            or (self.document_review_boundary == "stale-hash" and call == 5)
        ):
            self.boundary_output = _latest_tool_output(input)
        if stage_key in {"policy-recovery", "quota-recovery"} and call == 2:
            self.failed_tool_outputs[stage_key] = _latest_tool_output(input)
        self.tool_names.append([tool.name for tool in tools])
        output = _scripted_output(
            stage_key,
            call,
            input,
            document_review_boundary=self.document_review_boundary,
            pause_continue_boundary=self.pause_continue_boundary,
        )
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
    *,
    document_review_boundary: Literal["protected-write", "stale-hash"] | None,
    pause_continue_boundary: Literal["invalid-checkpoint"] | None,
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

    if stage == "document-review-editor":
        if document_review_boundary == "protected-write":
            if call == 1:
                return [_overwrite("overwrite_brief", BRIEF_PATH, "tampered\n")]
            assert _latest_tool_output(input)["ok"] is False
            return [_message("Protected write rejected.")]
        if call == 1:
            return [_read("read_release_brief", BRIEF_PATH)]
        if call == 2:
            return [_read("read_review_instructions", INSTRUCTIONS_PATH)]
        if call == 3:
            return [_read("read_release_draft", DRAFT_PATH)]
        if call == 4:
            content_hash = (
                "0" * 64 if document_review_boundary == "stale-hash" else _latest_hash(input)
            )
            return [_document_review_patch(content_hash)]
        if call == 5:
            if document_review_boundary == "stale-hash":
                assert _latest_tool_output(input)["ok"] is False
                return [_message("Stale draft update rejected.")]
            return [_read("verify_release_draft", DRAFT_PATH)]
        return [_message("Release draft corrected.")]

    if stage == "document-review-reviewer":
        if call == 1:
            return [_read("review_release_brief", BRIEF_PATH)]
        if call == 2:
            return [_read("review_instructions", INSTRUCTIONS_PATH)]
        if call == 3:
            return [_read("review_release_draft", DRAFT_PATH)]
        if call == 4:
            return [
                _write(
                    "write_document_review",
                    DOCUMENT_REVIEW_PATH,
                    DOCUMENT_REVIEW_CONTENT.decode(),
                )
            ]
        if call == 5:
            return [_read("verify_document_review", DOCUMENT_REVIEW_PATH)]
        return [_message("Document review approved.")]

    if stage == "independent-reviewers-baseline":
        if call == 1:
            return [_read("read_review_source", REVIEW_SOURCE_PATH)]
        if call == 2:
            return [_read("read_review_criteria", REVIEW_CRITERIA_PATH)]
        if call == 3:
            return [_read("read_review_status", REVIEW_STATUS_PATH)]
        return [_message("Reviewer baseline prepared.")]

    if stage in {"independent-reviewer-risk", "independent-reviewer-clarity"}:
        perspective = stage.removeprefix("independent-reviewer-")
        if call == 1:
            return [_read(f"read_{perspective}_source", REVIEW_SOURCE_PATH)]
        if call == 2:
            return [_read(f"read_{perspective}_criteria", REVIEW_CRITERIA_PATH)]
        if call == 3:
            return [_read(f"read_{perspective}_status", REVIEW_STATUS_PATH)]
        if perspective == "risk":
            status = RISK_REVIEW_STATUS
            content = RISK_REVIEW_CONTENT
            completion = "Risk review complete."
        else:
            status = CLARITY_REVIEW_STATUS
            content = CLARITY_REVIEW_CONTENT
            completion = "Clarity review complete."
        if call == 4:
            return [
                _patch(
                    f"set_{perspective}_status",
                    REVIEW_STATUS_PATH,
                    BASELINE_REVIEW_STATUS.decode().strip(),
                    status.decode().strip(),
                    _latest_hash(input),
                )
            ]
        if call == 5:
            return [
                _write(
                    f"write_{perspective}_review",
                    REVIEW_FINDINGS_PATH,
                    content.decode(),
                )
            ]
        if call == 6:
            return [_read(f"verify_{perspective}_review", REVIEW_FINDINGS_PATH)]
        return [_message(completion)]

    if stage == "pause-continue-initial":
        if call == 1:
            return [_read("read_pause_request", REQUEST_PATH)]
        if call == 2:
            return [_read("read_pause_status", STATUS_PATH)]
        if call == 3:
            return [
                _patch(
                    "pause_work",
                    STATUS_PATH,
                    "state=ready",
                    "state=paused",
                    _latest_hash(input),
                )
            ]
        if call == 4:
            content = (
                _INVALID_CHECKPOINT_CONTENT
                if pause_continue_boundary == "invalid-checkpoint"
                else CHECKPOINT_CONTENT
            )
            return [
                _write(
                    "write_pause_checkpoint",
                    CHECKPOINT_PATH,
                    content.decode(),
                )
            ]
        if call == 5:
            return [_read("verify_pause_checkpoint", CHECKPOINT_PATH)]
        return [_message("Checkpoint saved.")]

    if stage == "pause-continue-resumed":
        if call == 1:
            return [_read("read_saved_checkpoint", CHECKPOINT_PATH)]
        if call == 2:
            return [_read("read_resumed_status", STATUS_PATH)]
        if call == 3:
            return [
                _patch(
                    "complete_resumed_work",
                    STATUS_PATH,
                    "state=paused",
                    "state=completed",
                    _latest_hash(input),
                )
            ]
        if call == 4:
            return [
                _write(
                    "write_continuation_result",
                    CONTINUATION_PATH,
                    CONTINUATION_CONTENT.decode(),
                )
            ]
        if call == 5:
            return [_read("verify_continuation_result", CONTINUATION_PATH)]
        return [_message("Continuation complete.")]

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
            return [_read("read_evidence", EVIDENCE_PATH)]
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


def _overwrite(call_id: str, path: str, content: str) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id=call_id,
        name="write_file",
        arguments={
            "path": path,
            "content": content,
            "write_condition": "any_current_state",
            "create_parents": False,
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


def _document_review_patch(content_hash: str) -> ResponseFunctionToolCall:
    return _tool_call(
        call_id="correct_release_draft",
        name="apply_patch",
        arguments={
            "patch": (
                f"--- {DRAFT_PATH}\n"
                f"+++ {DRAFT_PATH}\n"
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
            "expected_hashes": [{"path": DRAFT_PATH, "content_hash": content_hash}],
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
    assert list(_EXPECTED_SCENARIO_EVIDENCE) == _SCENARIO_NAMES
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


def test_document_review_contract_requires_discovery_and_independent_review() -> None:
    scenario = get_scenario("document-review")

    assert isinstance(scenario, StagedScenario)
    assert [stage.key for stage in scenario.stages] == [
        "document-review-editor",
        "document-review-reviewer",
    ]
    assert [artifact.path for artifact in scenario.expected_artifacts] == [
        BRIEF_PATH,
        INSTRUCTIONS_PATH,
        DRAFT_PATH,
        DOCUMENT_REVIEW_PATH,
    ]
    prompts = "\n".join(stage.prompt for stage in scenario.stages)
    assert "2.4.0" not in prompts
    assert "2026-09-18" not in prompts
    assert "3.12" not in prompts


def test_independent_reviewers_contract_forks_one_verified_baseline() -> None:
    scenario = get_scenario("independent-reviewers")

    assert isinstance(scenario, SnapshotBranchingScenario)
    assert scenario.baseline_stage.key == "independent-reviewers-baseline"
    assert [branch.name for branch in scenario.branches] == ["risk", "clarity"]
    assert [branch.stage.key for branch in scenario.branches] == [
        "independent-reviewer-risk",
        "independent-reviewer-clarity",
    ]
    assert scenario.selected_branch == "risk"
    assert [artifact.path for artifact in scenario.baseline_expected_artifacts] == [
        REVIEW_SOURCE_PATH,
        REVIEW_CRITERIA_PATH,
        REVIEW_STATUS_PATH,
    ]
    prompts = "\n".join(
        [scenario.baseline_stage.prompt] + [branch.stage.prompt for branch in scenario.branches]
    )
    assert "needs safeguards" not in prompts
    assert "revision requested" not in prompts


def test_pause_continue_contract_requires_workspace_discovery_across_fresh_runs() -> None:
    scenario = get_scenario("pause-continue")

    assert isinstance(scenario, PauseContinueScenario)
    assert scenario.initial_stage.key == "pause-continue-initial"
    assert scenario.continuation_stage.key == "pause-continue-resumed"
    assert [artifact.path for artifact in scenario.checkpoint_artifacts] == [
        REQUEST_PATH,
        STATUS_PATH,
        CHECKPOINT_PATH,
    ]
    assert [artifact.path for artifact in scenario.expected_artifacts] == [
        REQUEST_PATH,
        STATUS_PATH,
        CHECKPOINT_PATH,
        CONTINUATION_PATH,
    ]
    prompts = f"{scenario.initial_stage.prompt}\n{scenario.continuation_stage.prompt}"
    assert "orion-api" not in prompts
    assert "02:00 UTC" not in prompts
    assert "Checkpoint saved." not in scenario.continuation_stage.prompt


@pytest.mark.asyncio
async def test_list_scenarios_does_not_require_azure_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await main(["--list-scenarios"])

    output = capsys.readouterr().out
    for name in _SCENARIO_NAMES:
        assert f"{name}:" in output


@pytest.mark.asyncio
async def test_provider_application_runs_without_network_and_closes_client(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = RecordingModelClient()
    model = DeterministicScenarioModel()

    await run_provider_sample(
        argv=["--scenario", "workspace-edit"],
        parser=build_sample_parser(description="Test provider"),
        model_factory=lambda: (client, model),
        client_name="test client",
    )

    assert client.closed is True
    assert "Verified /workspace/demo/report.txt:" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_provider_application_reports_pause_continue_lifecycle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = RecordingModelClient()
    model = DeterministicScenarioModel()

    await run_provider_sample(
        argv=["--scenario", "pause-continue"],
        parser=build_sample_parser(description="Test provider"),
        model_factory=lambda: (client, model),
        client_name="test client",
    )

    output = capsys.readouterr().out
    assert client.closed is True
    assert "Saved state: JSON-safe" in output
    assert "Live reattachment: reused source backend" in output
    assert "Replacement resume: restored into a distinct backend" in output
    assert f"Verified {CONTINUATION_PATH}:" in output


@pytest.mark.parametrize("scenario_name", _SCENARIO_NAMES)
@pytest.mark.asyncio
async def test_registered_scenario_runs_without_network_and_cleans_backends(
    scenario_name: str,
) -> None:
    scenario = get_scenario(scenario_name)
    expected = _EXPECTED_SCENARIO_EVIDENCE[scenario_name]
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
        assert (
            tuple((artifact.path, artifact.content) for artifact in result.artifacts)
            == expected.selected_artifacts
        )
        assert all(names == _EXPECTED_TOOLS for names in model.tool_names)
        if expected.branches:
            assert isinstance(scenario, SnapshotBranchingScenario)
            assert expected.selected_branch is not None
            assert result.selected_branch == expected.selected_branch
            assert (
                tuple(
                    ExpectedBranchEvidence(
                        name=branch_result.name,
                        artifacts=tuple(
                            (artifact.path, artifact.content)
                            for artifact in branch_result.artifacts
                        ),
                    )
                    for branch_result in result.branch_results
                )
                == expected.branches
            )
            assert (
                tuple(branch_result.output for branch_result in result.branch_results)
                == (result.stage_outputs[1:])
            )
            assert (
                tuple((artifact.path, artifact.content) for artifact in result.baseline_artifacts)
                == expected.baseline_artifacts
            )
            selected_index = next(
                index
                for index, branch_result in enumerate(result.branch_results)
                if branch_result.name == expected.selected_branch
            )
            selected_result = result.branch_results[selected_index]
            assert result.artifacts == selected_result.artifacts
            assert selected_result.output == result.stage_outputs[selected_index + 1]
            expected_handles = 1 + len(expected.branches) + bool(expected.baseline_artifacts)
            assert len(service.created_handles) == expected_handles
        elif isinstance(scenario, PauseContinueScenario):
            assert expected.selected_branch is None
            assert result.resume_evidence is not None
            assert len(service.created_handles) == 2
        else:
            assert isinstance(scenario, StagedScenario)
            assert expected.selected_branch is None
            assert result.selected_branch is None
            assert len(service.created_handles) == 1
        assert tuple(model.stage_order) == expected.stage_order
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_document_review_guards_revision_and_verifies_evidence() -> None:
    scenario = get_scenario("document-review")
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel()
    try:
        result = await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
        )

        assert result.stage_outputs == (
            "Release draft corrected.",
            "Document review approved.",
        )
        assert [(artifact.path, artifact.content) for artifact in result.artifacts] == [
            (BRIEF_PATH, BRIEF_CONTENT),
            (INSTRUCTIONS_PATH, INSTRUCTIONS_CONTENT),
            (DRAFT_PATH, EXPECTED_DRAFT_CONTENT),
            (DOCUMENT_REVIEW_PATH, DOCUMENT_REVIEW_CONTENT),
        ]
        assert EXPECTED_DRAFT_CONTENT.endswith(
            b"## Upgrade notes\n\nBack up the workspace before upgrading.\n"
        )
        assert model.calls == {
            "document-review-editor": 6,
            "document-review-reviewer": 6,
        }
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.parametrize(
    ("boundary", "expected_code"),
    [
        ("protected-write", "session_policy_denied"),
        ("stale-hash", "stale_content"),
    ],
)
@pytest.mark.asyncio
async def test_document_review_rejects_unsafe_edits_without_mutation(
    boundary: Literal["protected-write", "stale-hash"],
    expected_code: str,
) -> None:
    scenario = get_scenario("document-review")
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(document_review_boundary=boundary)
    inspected: dict[str, bytes] = {}

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert isinstance(context.error, ScenarioVerificationError)
        inspected["brief"] = (await session.read_bytes(ReadBytesRequest(path=BRIEF_PATH))).content
        inspected["draft"] = (await session.read_bytes(ReadBytesRequest(path=DRAFT_PATH))).content

    try:
        with pytest.raises(
            ScenarioVerificationError,
            match=f"{DRAFT_PATH} did not contain the required final content",
        ):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
            )

        assert model.boundary_output is not None
        error = cast(dict[str, Any], model.boundary_output["error"])
        assert error["code"] == expected_code
        assert inspected == {
            "brief": BRIEF_CONTENT,
            "draft": DRAFT_CONTENT,
        }
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_document_review_reviewer_failure_preserves_editor_work_and_cleans_up() -> None:
    scenario = get_scenario("document-review")
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(
        failure=RuntimeError("review dependency failed"),
        failure_stage="document-review-reviewer",
    )
    inspected_draft: bytes | None = None

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        nonlocal inspected_draft
        assert isinstance(context.error, RuntimeError)
        inspected_draft = (await session.read_bytes(ReadBytesRequest(path=DRAFT_PATH))).content

    try:
        with pytest.raises(RuntimeError, match="review dependency failed"):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
            )

        assert inspected_draft == EXPECTED_DRAFT_CONTENT
        assert model.calls == {
            "document-review-editor": 6,
            "document-review-reviewer": 1,
        }
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_multi_agent_handoff_failure_preserves_plan_and_skips_reviewer() -> None:
    scenario = get_scenario("multi-agent-handoff")
    assert isinstance(scenario, StagedScenario)
    config_entry = scenario.manifest_factory().entries["app.conf"]
    assert isinstance(config_entry, File)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(
        failure=RuntimeError("implementation dependency failed"),
        failure_stage="multi-agent-implement",
    )
    inspected: dict[str, bytes] = {}

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert isinstance(context.error, RuntimeError)
        inspected["plan"] = (await session.read_bytes(ReadBytesRequest(path=PLAN_PATH))).content
        inspected["config"] = (await session.read_bytes(ReadBytesRequest(path=CONFIG_PATH))).content

    try:
        with pytest.raises(RuntimeError, match="implementation dependency failed"):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
            )

        assert inspected == {
            "plan": PLAN_CONTENT,
            "config": config_entry.content,
        }
        assert model.stage_order == ["multi-agent-plan", "multi-agent-implement"]
        assert model.calls == {
            "multi-agent-plan": 2,
            "multi-agent-implement": 1,
        }
        assert "multi-agent-review" not in model.calls
        assert len(service.created_handles) == 1
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_independent_reviewers_preserve_baseline_and_isolate_sibling_results() -> None:
    scenario = get_scenario("independent-reviewers")
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

        assert result.stage_outputs == (
            "Reviewer baseline prepared.",
            "Risk review complete.",
            "Clarity review complete.",
        )
        assert result.selected_branch == "risk"
        assert [(artifact.path, artifact.content) for artifact in result.baseline_artifacts] == [
            (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
            (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
            (REVIEW_STATUS_PATH, BASELINE_REVIEW_STATUS),
        ]
        assert [branch.name for branch in result.branch_results] == ["risk", "clarity"]
        assert [
            (artifact.path, artifact.content) for artifact in result.branch_results[0].artifacts
        ] == [
            (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
            (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
            (REVIEW_STATUS_PATH, RISK_REVIEW_STATUS),
            (REVIEW_FINDINGS_PATH, RISK_REVIEW_CONTENT),
        ]
        assert [
            (artifact.path, artifact.content) for artifact in result.branch_results[1].artifacts
        ] == [
            (REVIEW_SOURCE_PATH, REVIEW_SOURCE_CONTENT),
            (REVIEW_CRITERIA_PATH, REVIEW_CRITERIA_CONTENT),
            (REVIEW_STATUS_PATH, CLARITY_REVIEW_STATUS),
            (REVIEW_FINDINGS_PATH, CLARITY_REVIEW_CONTENT),
        ]
        assert result.artifacts == result.branch_results[0].artifacts
        assert model.calls == {
            "independent-reviewers-baseline": 4,
            "independent-reviewer-risk": 7,
            "independent-reviewer-clarity": 7,
        }
        assert len(service.created_handles) == 4
        assert len({str(handle) for handle in service.created_handles}) == 4
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_independent_reviewer_failure_inspects_untouched_fork_and_cleans_up() -> None:
    scenario = get_scenario("independent-reviewers")
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(
        failure=RuntimeError("clarity reviewer unavailable"),
        failure_stage="independent-reviewer-clarity",
    )
    inspected: dict[str, bytes] = {}

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert isinstance(context.error, RuntimeError)
        inspected["source"] = (
            await session.read_bytes(ReadBytesRequest(path=REVIEW_SOURCE_PATH))
        ).content
        inspected["status"] = (
            await session.read_bytes(ReadBytesRequest(path=REVIEW_STATUS_PATH))
        ).content

    try:
        with pytest.raises(RuntimeError, match="clarity reviewer unavailable"):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
                snapshot_store=bundle.snapshot_store,
                clock=bundle.clock,
            )

        assert inspected == {
            "source": REVIEW_SOURCE_CONTENT,
            "status": BASELINE_REVIEW_STATUS,
        }
        assert model.calls == {
            "independent-reviewers-baseline": 4,
            "independent-reviewer-risk": 7,
            "independent-reviewer-clarity": 1,
        }
        assert len(service.created_handles) == 3
        assert len({str(handle) for handle in service.created_handles}) == 3
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_continue_serializes_state_and_restores_into_a_fresh_run() -> None:
    scenario = get_scenario("pause-continue")
    assert isinstance(scenario, PauseContinueScenario)
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

        assert result.stage_outputs == (
            "Checkpoint saved.",
            "Continuation complete.",
        )
        assert [(artifact.path, artifact.content) for artifact in result.artifacts] == [
            (REQUEST_PATH, REQUEST_CONTENT),
            (STATUS_PATH, COMPLETED_STATUS),
            (CHECKPOINT_PATH, CHECKPOINT_CONTENT),
            (CONTINUATION_PATH, CONTINUATION_CONTENT),
        ]
        assert result.resume_evidence is not None
        serialized_state = json.loads(result.resume_evidence.saved_state_json)
        assert serialized_state["type"] == "mem_sandbox"
        assert serialized_state["provider_state_version"] == 1
        assert serialized_state["sandbox_handle"] == result.resume_evidence.source_handle
        assert serialized_state["workspace_archive_format_version"] is not None
        assert serialized_state["workspace_archive_revision"] is not None
        assert serialized_state["workspace_archive_root_hash"] is not None
        assert "service" not in serialized_state
        assert "session" not in serialized_state
        assert result.resume_evidence.live_reattached_handle == result.resume_evidence.source_handle
        assert result.resume_evidence.replacement_handle != result.resume_evidence.source_handle
        continuation_input = model.initial_inputs["pause-continue-resumed"]
        assert isinstance(continuation_input, list)
        assert len(continuation_input) == 1
        initial_item = cast(dict[str, Any], continuation_input[0])
        assert initial_item == {
            "content": scenario.continuation_stage.prompt,
            "role": "user",
        }
        assert "orion-api" not in scenario.continuation_stage.prompt
        assert "Checkpoint saved." not in scenario.continuation_stage.prompt
        assert model.calls == {
            "pause-continue-initial": 6,
            "pause-continue-resumed": 6,
        }
        assert len(service.created_handles) == 2
        assert len({str(handle) for handle in service.created_handles}) == 2
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_continue_rejects_invalid_checkpoint_before_persistence() -> None:
    scenario = get_scenario("pause-continue")
    assert isinstance(scenario, PauseContinueScenario)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    snapshot_store = RecordingSnapshotStore(bundle.snapshot_store)
    model = DeterministicScenarioModel(pause_continue_boundary="invalid-checkpoint")
    inspected_checkpoint: bytes | None = None

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        nonlocal inspected_checkpoint
        assert isinstance(context.error, ScenarioVerificationError)
        inspected_checkpoint = (
            await session.read_bytes(ReadBytesRequest(path=CHECKPOINT_PATH))
        ).content

    try:
        with pytest.raises(ScenarioVerificationError, match=CHECKPOINT_PATH):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
                snapshot_store=snapshot_store,
                clock=bundle.clock,
            )

        assert inspected_checkpoint == _INVALID_CHECKPOINT_CONTENT
        assert snapshot_store.save_calls == 0
        assert len(service.created_handles) == 1
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_continue_failure_inspects_restored_checkpoint_and_cleans_up() -> None:
    scenario = get_scenario("pause-continue")
    assert isinstance(scenario, PauseContinueScenario)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(
        failure=RuntimeError("continuation dependency failed"),
        failure_stage="pause-continue-resumed",
    )
    inspected: dict[str, bytes] = {}

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert isinstance(context.error, RuntimeError)
        inspected["checkpoint"] = (
            await session.read_bytes(ReadBytesRequest(path=CHECKPOINT_PATH))
        ).content
        inspected["status"] = (await session.read_bytes(ReadBytesRequest(path=STATUS_PATH))).content

    try:
        with pytest.raises(RuntimeError, match="continuation dependency failed"):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
                snapshot_store=bundle.snapshot_store,
                clock=bundle.clock,
            )

        assert inspected == {
            "checkpoint": CHECKPOINT_CONTENT,
            "status": PAUSED_STATUS,
        }
        assert model.calls == {
            "pause-continue-initial": 6,
            "pause-continue-resumed": 1,
        }
        assert len(service.created_handles) == 2
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_continue_missing_saved_state_fails_before_replacement_allocation() -> None:
    scenario = get_scenario("pause-continue")
    assert isinstance(scenario, PauseContinueScenario)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    snapshot_store = MissingSnapshotStore(bundle.snapshot_store)
    model = DeterministicScenarioModel()
    try:
        with pytest.raises(SnapshotNotRestorableError):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                snapshot_store=snapshot_store,
                clock=bundle.clock,
            )

        assert model.calls == {"pause-continue-initial": 6}
        assert snapshot_store.load_calls >= 1
        assert len(service.created_handles) == 1
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pause_continue_cancellation_during_replacement_resume_load_cleans_source() -> None:
    scenario = get_scenario("pause-continue")
    assert isinstance(scenario, PauseContinueScenario)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    snapshot_store = CancelOnReplacementResumeLoadSnapshotStore(
        bundle.snapshot_store,
        service,
    )
    model = DeterministicScenarioModel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                snapshot_store=snapshot_store,
                clock=bundle.clock,
            )

        assert model.stage_order == ["pause-continue-initial"]
        assert model.calls == {"pause-continue-initial": 6}
        state = snapshot_store.state_at_cancellation
        assert state is not None
        assert len(state.created_handles) == 1
        source_handle = state.created_handles[0]
        assert set(state.deleted_handles) == {source_handle}
        assert state.source_backend_missing is True
        assert service.created_handles == [source_handle]
        assert set(service.deleted_handles) == {source_handle}
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
async def test_snapshot_branching_fork_failure_preserves_completed_sibling_and_cleans_up() -> None:
    scenario = get_scenario("snapshot-branching")
    assert isinstance(scenario, SnapshotBranchingScenario)
    manifest = scenario.manifest_factory()
    choice_entry = manifest.entries["choice.txt"]
    plan_entry = manifest.entries["branch-plan.txt"]
    assert isinstance(choice_entry, File)
    assert isinstance(plan_entry, File)
    branches = {branch.name: branch for branch in scenario.branches}
    conservative_expected = {
        artifact.path: artifact.content for artifact in branches["conservative"].expected_artifacts
    }
    failing_expected = {
        CHOICE_PATH: choice_entry.content,
        BRANCH_PLAN_PATH: plan_entry.content,
        **{artifact.path: artifact.content for artifact in scenario.checkpoint_artifacts},
    }
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(
        failure=RuntimeError("aggressive branch dependency failed"),
        failure_stage="snapshot-aggressive",
    )
    inspected: dict[str, dict[str, bytes]] = {}
    inspected_handles: list[SandboxHandle] = []

    async def inspect(context: InspectionContext, session: SandboxSession) -> None:
        assert isinstance(context.error, RuntimeError)
        assert len(service.created_handles) == 3
        source_handle, conservative_handle, aggressive_handle = service.created_handles
        with pytest.raises(SandboxNotFound):
            await service.delegate.get_session(source_handle)
        conservative_session = await service.delegate.get_session(conservative_handle)
        aggressive_session = await service.delegate.get_session(aggressive_handle)
        assert session.session_id == aggressive_session.session_id
        assert session.session_id != conservative_session.session_id
        inspected_handles.extend((conservative_handle, aggressive_handle))
        inspected["completed"] = {
            path: (await conservative_session.read_bytes(ReadBytesRequest(path=path))).content
            for path in conservative_expected
        }
        inspected["failing"] = {
            path: (await session.read_bytes(ReadBytesRequest(path=path))).content
            for path in failing_expected
        }

    try:
        with pytest.raises(RuntimeError, match="aggressive branch dependency failed"):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                inspector=inspect,
                inspect_on_failure=True,
                snapshot_store=bundle.snapshot_store,
                clock=bundle.clock,
            )

        assert inspected == {
            "completed": conservative_expected,
            "failing": failing_expected,
        }
        assert inspected_handles == service.created_handles[1:]
        assert model.stage_order == [
            "snapshot-baseline",
            "snapshot-conservative",
            "snapshot-aggressive",
        ]
        assert model.calls == {
            "snapshot-baseline": 2,
            "snapshot-conservative": 3,
            "snapshot-aggressive": 1,
        }
        assert len({str(handle) for handle in service.created_handles}) == 3
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_snapshot_branching_cancellation_at_aggressive_branch_cleans_all_backends() -> None:
    scenario = get_scenario("snapshot-branching")
    assert isinstance(scenario, SnapshotBranchingScenario)
    bundle = create_sample_service_bundle(policy_engine=scenario.policy_engine_factory())
    service = RecordingService(bundle.service)
    model = DeterministicScenarioModel(
        failure=asyncio.CancelledError(),
        failure_stage="snapshot-aggressive",
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_scenario(
                model=model,
                service=service,
                scenario=scenario,
                snapshot_store=bundle.snapshot_store,
                clock=bundle.clock,
            )

        assert model.stage_order == [
            "snapshot-baseline",
            "snapshot-conservative",
            "snapshot-aggressive",
        ]
        assert model.calls == {
            "snapshot-baseline": 2,
            "snapshot-conservative": 3,
            "snapshot-aggressive": 1,
        }
        assert len(service.created_handles) == 3
        assert len({str(handle) for handle in service.created_handles}) == 3
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
async def test_policy_recovery_retains_structured_denial_and_exact_recovery() -> None:
    scenario = get_scenario("policy-recovery")
    assert isinstance(scenario, StagedScenario)
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
        result = await run_scenario(
            model=model,
            service=service,
            scenario=scenario,
            inspector=inspect,
            inspect_success=True,
            snapshot_store=bundle.snapshot_store,
            clock=bundle.clock,
        )

        failed_output = model.failed_tool_outputs["policy-recovery"]
        assert failed_output["ok"] is False
        error = failed_output["error"]
        assert isinstance(error, dict)
        assert {
            "category": error["category"],
            "code": error["code"],
            "correctable": error["correctable"],
            "message": error["message"],
            "retryable": error["retryable"],
        } == {
            "category": "policy_denied",
            "code": "session_policy_denied",
            "correctable": False,
            "message": "policy denied execute: sample_execute_denied",
            "retryable": False,
        }
        assert [(artifact.path, artifact.content) for artifact in result.artifacts] == [
            (artifact.path, artifact.content) for artifact in scenario.expected_artifacts
        ]
        artifacts = {artifact.path: artifact.content for artifact in result.artifacts}
        assert artifacts[EVIDENCE_PATH] == EVIDENCE_CONTENT
        assert artifacts[POLICY_REPORT_PATH] == POLICY_REPORT
        assert EVIDENCE_CONTENT.decode() in cli_output.getvalue()
        await _assert_backends_deleted(service)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_quota_recovery_retains_structured_bound_and_exact_compact_retry() -> None:
    scenario = get_scenario("quota-recovery")
    assert isinstance(scenario, StagedScenario)
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

        failed_output = model.failed_tool_outputs["quota-recovery"]
        assert failed_output["ok"] is False
        error = failed_output["error"]
        assert isinstance(error, dict)
        assert {
            "category": error["category"],
            "code": error["code"],
            "correctable": error["correctable"],
            "message": error["message"],
            "retryable": error["retryable"],
        } == {
            "category": "quota_exceeded",
            "code": "file_size_limit_exceeded",
            "correctable": True,
            "message": "file contains 100 bytes; limit is 64 bytes",
            "retryable": False,
        }
        assert scenario.options_factory().workspace_limits.max_file_bytes == 64
        assert [(artifact.path, artifact.content) for artifact in result.artifacts] == [
            (artifact.path, artifact.content) for artifact in scenario.expected_artifacts
        ]
        artifacts = {artifact.path: artifact.content for artifact in result.artifacts}
        assert artifacts[QUOTA_PATH] == QUOTA_CONTENT
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
