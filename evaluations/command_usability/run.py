from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from mem_sandbox.core import SandboxError, SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    CreateSandboxRequest,
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
    OwnerId,
    WorkspaceSeedFile,
)
from mem_sandbox.session import (
    ApplyPatchRequest,
    ReadBytesRequest,
    ReadFileRequest,
    SandboxSession,
    SessionExecuteRequest,
    WriteFileRequest,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)
from mem_sandbox.workspace import PathNotFoundError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = Path(__file__).with_name("corpus-v1.json")
DEFAULT_MODELS = ("gpt-5.6-sol", "claude-sonnet-5", "gemini-3.7-flash")


@dataclass(frozen=True, slots=True)
class Scenario:
    task_id: str
    category: str
    prompt: str
    seed_files: tuple[WorkspaceSeedFile, ...]
    checks: tuple[Mapping[str, object], ...]


@dataclass(slots=True)
class TaskMetrics:
    task_id: str
    completed: bool = False
    first_attempt_parser_registry_accepted: bool | None = None
    tool_calls: int = 0
    repair_turns: int = 0
    output_bytes: int = 0
    truncated: bool = False
    tool_latency_ms: float = 0.0
    failures: list[str] = field(default_factory=list[str])
    final_answer: str | None = None


@dataclass(frozen=True, slots=True)
class ModelTurn:
    turn: int
    latency_ms: float
    response_bytes: int
    usage: Mapping[str, object]


def load_corpus(path: Path = DEFAULT_CORPUS) -> tuple[Scenario, ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    tasks = cast(list[dict[str, object]], value["tasks"])
    return tuple(
        Scenario(
            task_id=cast(str, item["id"]),
            category=cast(str, item["category"]),
            prompt=cast(str, item["prompt"]),
            seed_files=tuple(
                WorkspaceSeedFile(
                    path=cast(str, seed["path"]),
                    content=cast(str, seed["content"]).encode("utf-8")
                    * _positive_integer(seed.get("repeat", 1), "repeat"),
                )
                for seed in cast(list[dict[str, object]], item["seed_files"])
            ),
            checks=tuple(cast(list[Mapping[str, object]], item["checks"])),
        )
        for item in tasks
    )


class EvaluationRuntime:
    def __init__(self, scenarios: Sequence[Scenario]) -> None:
        clock = SystemClock()
        uuids = SystemUuidGenerator()
        codec = JsonSessionSnapshotCodec()
        store = InMemorySnapshotStore(
            default_ttl=timedelta(hours=1),
            limits=SnapshotStoreLimits(
                max_snapshots=100,
                max_total_payload_bytes=64 * 1024 * 1024,
            ),
            clock=clock,
        )
        self._service = InMemorySandboxService(
            session_factory=DefaultSessionFactory(
                policy_engine=AllowAllPolicyEngine(),
                secret_broker=NoSecretBroker(),
                event_sink=InMemoryEventSink(
                    max_events=10_000,
                    max_payload_bytes=16 * 1024 * 1024,
                ),
                snapshot_codec=codec,
                clock=clock,
                uuid_generator=uuids,
            ),
            snapshot_gateway=InMemoryServiceSnapshotGateway(store),
            snapshot_decoder=codec,
            clock=clock,
            uuid_generator=uuids,
        )
        self._scenarios = {scenario.task_id: scenario for scenario in scenarios}
        self._sessions: dict[str, SandboxSession] = {}

    async def start(self) -> None:
        for scenario in self._scenarios.values():
            handle = await self._service.create(
                CreateSandboxRequest(
                    owner_id=OwnerId(f"evaluation-{scenario.task_id.lower()}"),
                    initial_files=scenario.seed_files,
                )
            )
            self._sessions[scenario.task_id] = await self._service.get_session(handle)

    async def close(self) -> None:
        await self._service.close()

    async def invoke(
        self,
        task_id: str,
        tool: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        session = self._sessions[task_id]
        started = time.perf_counter()
        try:
            if tool == "execute":
                result = await session.execute(
                    SessionExecuteRequest(command=_text_argument(arguments, "command"))
                )
                return {
                    "ok": True,
                    "exit_code": result.exit_code,
                    "failure_code": (
                        result.failure_code.value if result.failure_code is not None else None
                    ),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "stdout_original_bytes": result.stdout_original_bytes,
                    "stderr_original_bytes": result.stderr_original_bytes,
                    "stdout_truncated": result.stdout_truncated,
                    "stderr_truncated": result.stderr_truncated,
                    "resulting_cwd": result.resulting_cwd.value,
                    "latency_ms": _elapsed_ms(started),
                }
            if tool == "read_file":
                result = await session.read_file(
                    ReadFileRequest(path=_text_argument(arguments, "path"))
                )
                return {
                    "ok": True,
                    "path": result.path.value,
                    "content": result.content,
                    "total_lines": result.total_lines,
                    "latency_ms": _elapsed_ms(started),
                }
            if tool == "write_file":
                result = await session.write_file(
                    WriteFileRequest(
                        path=_text_argument(arguments, "path"),
                        content=_text_argument(arguments, "content"),
                        create_parents=_bool_argument(arguments, "create_parents", False),
                    )
                )
                return {
                    "ok": True,
                    "path": result.path.value,
                    "created": result.created,
                    "changed": result.changed,
                    "latency_ms": _elapsed_ms(started),
                }
            if tool == "apply_patch":
                result = await session.apply_patch(
                    ApplyPatchRequest(patch=_text_argument(arguments, "patch"))
                )
                return {
                    "ok": True,
                    "paths": [file.path.value for file in result.files],
                    "latency_ms": _elapsed_ms(started),
                }
            return {
                "ok": False,
                "error_code": "unknown_tool",
                "error": f"unsupported evaluation tool: {tool}",
                "latency_ms": _elapsed_ms(started),
            }
        except (SandboxError, TypeError, ValueError) as error:
            return {
                "ok": False,
                "error_code": getattr(error, "code", type(error).__name__),
                "error": str(error),
                "latency_ms": _elapsed_ms(started),
            }

    async def evaluate(self, task_id: str, answer: str) -> tuple[bool, tuple[str, ...]]:
        session = self._sessions[task_id]
        failures: list[str] = []
        for check in self._scenarios[task_id].checks:
            kind = cast(str, check["kind"])
            if kind == "file_text":
                path = cast(str, check["path"])
                expected = cast(str, check["expected"])
                try:
                    actual = (await session.read_bytes(ReadBytesRequest(path=path))).content
                except SandboxError as error:
                    failures.append(f"{path}: {error.code}")
                else:
                    if actual != expected.encode("utf-8"):
                        failures.append(f"{path}: content mismatch")
            elif kind == "absent":
                path = cast(str, check["path"])
                try:
                    await session.read_file(ReadFileRequest(path=path))
                except PathNotFoundError:
                    pass
                except SandboxError as error:
                    failures.append(f"{path}: expected absent, got {error.code}")
                else:
                    failures.append(f"{path}: expected absent")
            elif kind == "answer_equals":
                expected = _normalize_answer(cast(str, check["expected"]))
                if _normalize_answer(answer) != expected:
                    failures.append(f"answer must equal {expected!r}")
            elif kind == "answer_contains_all":
                expected_values = cast(list[str], check["expected"])
                missing = [value for value in expected_values if value not in answer]
                if missing:
                    failures.append(f"answer missing {missing!r}")
            else:
                failures.append(f"unsupported check kind: {kind}")
        return not failures, tuple(failures)


def _text_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bool_argument(
    arguments: Mapping[str, object],
    name: str,
    default: bool,
) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _normalize_answer(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.strip().splitlines())


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _initial_prompt(scenarios: Sequence[Scenario]) -> str:
    tasks = [{"task_id": item.task_id, "prompt": item.prompt} for item in scenarios]
    return f"""You are evaluating a constrained in-memory coding sandbox.
Complete every task through the available abstract tools. Each task has an independent
workspace and all paths are POSIX paths under /workspace.

Tools:
- execute(task_id, command): registered virtual commands only. Supported commands are
  pwd, cd, ls, cat, echo, mkdir, touch, rm, head, tail, grep, find, wc, sort, uniq, cp,
  mv, env, export, and unset. Supported composition is |, ;, &&, >, and >>.
- read_file(task_id, path): read one UTF-8 file.
- write_file(task_id, path, content, create_parents): write exact UTF-8 content.
- apply_patch(task_id, patch): apply one unified diff.
- final(task_id, answer): submit the task. Use an empty answer for mutation-only tasks.

Return JSON only, with exactly one next action for every unfinished task:
{{"actions":[{{"task_id":"T1","tool":"execute","arguments":{{"command":"pwd"}}}}]}}
Do not call host tools, assume unlisted shell behavior, or combine multiple actions for
one task in the same response.

Tasks:
{json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))}
"""


def _feedback_prompt(observations: Sequence[Mapping[str, object]]) -> str:
    return (
        "Continue every unfinished task. Here are the latest tool observations. "
        "Return JSON only with exactly one next action per unfinished task, using the "
        "same schema. Repair any reported failure before submitting final.\n"
        + json.dumps(observations, ensure_ascii=False, separators=(",", ":"))
    )


def _parse_actions(response: str) -> tuple[Mapping[str, object], ...]:
    start = response.find("{")
    end = response.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response did not contain a JSON object")
    raw_value: object = json.loads(response[start : end + 1])
    if not isinstance(raw_value, dict):
        raise ValueError("model response must be an object")
    value = cast(dict[str, object], raw_value)
    raw_actions = value.get("actions")
    if not isinstance(raw_actions, list):
        raise ValueError("model response must contain an actions list")
    actions: list[Mapping[str, object]] = []
    for action in cast(list[object], raw_actions):
        if not isinstance(action, dict):
            raise ValueError("every action must be an object")
        actions.append(cast(dict[str, object], action))
    return tuple(actions)


def _invoke_model(
    *,
    model: str,
    effort: str,
    session_id: str,
    prompt: str,
    turn: int,
    output_dir: Path,
) -> tuple[str, ModelTurn]:
    usage_path = output_dir / f"turn-{turn:02d}-usage.json"
    command = [
        "copilot",
        "-p",
        prompt,
        "--model",
        model,
        "--effort",
        effort,
        "--no-custom-instructions",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-auto-update",
        "--available-tools=",
        "--output-format",
        "json",
        "--usage-output-file",
        str(usage_path),
    ]
    if turn == 1:
        command.extend(("--session-id", session_id))
    else:
        command.append(f"--resume={session_id}")
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    response = assistant_content(result.stdout)
    usage = cast(dict[str, object], json.loads(usage_path.read_text(encoding="utf-8")))
    return response, ModelTurn(
        turn=turn,
        latency_ms=_elapsed_ms(started),
        response_bytes=len(response.encode("utf-8")),
        usage=usage,
    )


def assistant_content(output: str) -> str:
    messages: list[str] = []
    for line in output.splitlines():
        try:
            event_value: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event_value, dict):
            continue
        event = cast(dict[str, object], event_value)
        if event.get("type") != "assistant.message":
            continue
        raw_data = event.get("data")
        if not isinstance(raw_data, dict):
            continue
        data = cast(dict[str, object], raw_data)
        content = data.get("content")
        if isinstance(content, str):
            messages.append(content)
    if not messages:
        raise ValueError("Copilot CLI output did not contain an assistant message")
    return messages[-1]


async def run_sample(
    *,
    scenarios: Sequence[Scenario],
    model: str,
    sample: int,
    effort: str,
    max_turns: int,
    output_dir: Path,
) -> dict[str, object]:
    runtime = EvaluationRuntime(scenarios)
    await runtime.start()
    metrics = {scenario.task_id: TaskMetrics(scenario.task_id) for scenario in scenarios}
    active = set(metrics)
    repair_pending: set[str] = set()
    turns: list[ModelTurn] = []
    observations: list[Mapping[str, object]] = []
    transcript: list[Mapping[str, object]] = []
    session_id = str(uuid4())
    try:
        for turn in range(1, max_turns + 1):
            if not active:
                break
            prompt = _initial_prompt(scenarios) if turn == 1 else _feedback_prompt(observations)
            response, turn_metrics = await asyncio.to_thread(
                _invoke_model,
                model=model,
                effort=effort,
                session_id=session_id,
                prompt=prompt,
                turn=turn,
                output_dir=output_dir,
            )
            turns.append(turn_metrics)
            (output_dir / f"turn-{turn:02d}-response.txt").write_text(
                response,
                encoding="utf-8",
            )
            observations = []
            try:
                actions = _parse_actions(response)
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                observations.append(
                    {
                        "protocol_error": str(error),
                        "unfinished_tasks": sorted(active),
                    }
                )
                transcript.append(
                    {
                        "turn": turn,
                        "protocol_error": str(error),
                        "response": response,
                    }
                )
                continue

            seen: set[str] = set()
            for action in actions:
                task_id = action.get("task_id")
                tool = action.get("tool")
                raw_arguments = action.get("arguments", {})
                if (
                    not isinstance(task_id, str)
                    or task_id not in active
                    or task_id in seen
                    or not isinstance(tool, str)
                    or not isinstance(raw_arguments, dict)
                ):
                    continue
                arguments = cast(dict[str, object], raw_arguments)
                seen.add(task_id)
                task_metrics = metrics[task_id]
                if tool == "final":
                    raw_answer = arguments.get("answer", "")
                    answer = raw_answer if isinstance(raw_answer, str) else ""
                    accepted, failures = await runtime.evaluate(task_id, answer)
                    task_metrics.final_answer = answer
                    if accepted:
                        task_metrics.completed = True
                        active.remove(task_id)
                    else:
                        task_metrics.failures.extend(failures)
                    observations.append(
                        {
                            "task_id": task_id,
                            "tool": tool,
                            "accepted": accepted,
                            "failures": failures,
                        }
                    )
                    transcript.append(
                        {
                            "turn": turn,
                            "task_id": task_id,
                            "tool": tool,
                            "arguments": arguments,
                            "accepted": accepted,
                            "failures": failures,
                        }
                    )
                    continue

                if task_id in repair_pending:
                    task_metrics.repair_turns += 1
                task_metrics.tool_calls += 1
                result = await runtime.invoke(
                    task_id,
                    tool,
                    arguments,
                )
                task_metrics.tool_latency_ms += cast(float, result["latency_ms"])
                task_metrics.output_bytes += len(
                    (
                        cast(str, result.get("stdout", ""))
                        + cast(str, result.get("stderr", ""))
                        + cast(str, result.get("content", ""))
                    ).encode("utf-8")
                )
                task_metrics.truncated = task_metrics.truncated or bool(
                    result.get("stdout_truncated", False) or result.get("stderr_truncated", False)
                )
                parser_or_registry_rejected = (
                    result.get("error_code")
                    in {"command_syntax_invalid", "command_syntax_unsupported"}
                    or result.get("failure_code") == "command_not_found"
                )
                if task_metrics.first_attempt_parser_registry_accepted is None:
                    task_metrics.first_attempt_parser_registry_accepted = (
                        not parser_or_registry_rejected
                    )
                if parser_or_registry_rejected:
                    repair_pending.add(task_id)
                else:
                    repair_pending.discard(task_id)
                if not cast(bool, result["ok"]) or result.get("failure_code") is not None:
                    task_metrics.failures.append(
                        str(result.get("error_code") or result.get("failure_code"))
                    )
                observation = {
                    "task_id": task_id,
                    "tool": tool,
                    "result": result,
                }
                observations.append(observation)
                transcript.append(
                    {
                        "turn": turn,
                        "task_id": task_id,
                        "tool": tool,
                        "arguments": arguments,
                        "result": result,
                    }
                )

            for missing in sorted(active - seen):
                observations.append(
                    {
                        "task_id": missing,
                        "protocol_error": "no action returned for unfinished task",
                    }
                )
    finally:
        await runtime.close()

    return {
        "model": model,
        "sample": sample,
        "session_id": session_id,
        "completed_tasks": sum(item.completed for item in metrics.values()),
        "task_count": len(metrics),
        "tasks": [asdict(metrics[key]) for key in sorted(metrics)],
        "model_turns": [asdict(item) for item in turns],
        "transcript": transcript,
    }


def _environment() -> dict[str, object]:
    version = subprocess.run(
        ["copilot", "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "copilot_cli": version,
        "commit": commit,
        "working_tree_dirty": bool(status.strip()),
        "evaluated_tree_hash": _evaluated_tree_hash(),
    }


def _evaluated_tree_hash() -> str:
    paths = [
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted(Path(__file__).parent.glob("*.py")),
        DEFAULT_CORPUS,
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _corpus_metrics(scenarios: Sequence[Scenario]) -> dict[str, int]:
    seed_files = tuple(seed for scenario in scenarios for seed in scenario.seed_files)
    return {
        "task_count": len(scenarios),
        "seed_file_count": len(seed_files),
        "seed_bytes": sum(len(seed.content) for seed in seed_files),
        "largest_seed_file_bytes": max((len(seed.content) for seed in seed_files), default=0),
    }


def _usage_integer(run: Mapping[str, object], *path: str) -> int:
    value: object = cast(list[Mapping[str, object]], run["model_turns"])[-1]["usage"]
    for key in path:
        if not isinstance(value, dict):
            return 0
        value = cast(dict[str, object], value).get(key)
    return value if isinstance(value, int) else 0


def render_report(report: Mapping[str, object]) -> str:
    runs = cast(list[Mapping[str, object]], report["runs"])
    lines = [
        "# Command Usability Evaluation Results",
        "",
        "The versioned corpus was executed through public `SandboxSession` tools. "
        "Model/provider measurements are observational and are not CI gates.",
        "",
        "| Model | Sample | Completed | First parser/registry acceptance | Tool calls | "
        "Repair turns | Input tokens | Output tokens | Provider latency (ms) | "
        "Tool output bytes | Truncated tasks |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        tasks = cast(list[Mapping[str, object]], run["tasks"])
        accepted = sum(item.get("first_attempt_parser_registry_accepted") is True for item in tasks)
        measured = sum(
            item.get("first_attempt_parser_registry_accepted") is not None for item in tasks
        )
        lines.append(
            "| "
            f"{run['model']} | {run['sample']} | {run['completed_tasks']}/{run['task_count']} | "
            f"{accepted}/{measured} | "
            f"{sum(cast(int, item['tool_calls']) for item in tasks)} | "
            f"{sum(cast(int, item['repair_turns']) for item in tasks)} | "
            f"{_usage_integer(run, 'modelMetrics', cast(str, run['model']), 'usage', 'inputTokens')} | "
            f"{_usage_integer(run, 'modelMetrics', cast(str, run['model']), 'usage', 'outputTokens')} | "
            f"{_usage_integer(run, 'totalApiDurationMs')} | "
            f"{sum(cast(int, item['output_bytes']) for item in tasks)} | "
            f"{sum(item['truncated'] is True for item in tasks)} |"
        )

    incomplete = [
        f"{run['model']} sample {run['sample']}: "
        + ", ".join(
            cast(str, task["task_id"])
            for task in cast(list[Mapping[str, object]], run["tasks"])
            if task["completed"] is not True
        )
        for run in runs
        if cast(int, run["completed_tasks"]) < cast(int, run["task_count"])
    ]
    lines.extend(
        (
            "",
            "## Remaining gaps",
            "",
            *(
                [f"- {item}" for item in incomplete]
                if incomplete
                else ["- Every sampled task completed within the configured turn budget."]
            ),
            "",
            "Provider token and latency totals are recorded per multi-task model session. "
            "Task-level metrics cover sandbox calls, repair turns, output size, and "
            "truncation. See `results.json` and each sample transcript for raw evidence.",
            "",
        )
    )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    scenarios = load_corpus(args.corpus)
    args.output.mkdir(parents=True, exist_ok=False)
    runs: list[dict[str, object]] = []
    for model in args.models:
        for sample in range(1, args.samples + 1):
            sample_dir = args.output / f"{model}-sample-{sample}"
            sample_dir.mkdir()
            result = await run_sample(
                scenarios=scenarios,
                model=model,
                sample=sample,
                effort=args.effort,
                max_turns=args.max_turns,
                output_dir=sample_dir,
            )
            runs.append(result)
            (sample_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    report = {
        "schema_version": 1,
        "corpus": str(args.corpus.relative_to(ROOT)),
        "corpus_metrics": _corpus_metrics(scenarios),
        "environment": _environment(),
        "runs": runs,
    }
    (args.output / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "REPORT.md").write_text(
        render_report(report),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
