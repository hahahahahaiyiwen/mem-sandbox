from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, cast

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

REPOSITORY_ROOT = Path(__file__).parents[3]
CORE_PROJECT_ROOT = REPOSITORY_ROOT
ADAPTER_PROJECT_ROOT = REPOSITORY_ROOT / "packages" / "openai-agents"
CORE_PACKAGE_ROOT = CORE_PROJECT_ROOT / "src" / "mem_sandbox"
ADAPTER_PACKAGE_ROOT = ADAPTER_PROJECT_ROOT / "src" / "mem_sandbox_openai_agents"
LEGACY_ADAPTER_ROOT = CORE_PACKAGE_ROOT / "integrations" / "openai_agents"
LEGACY_STATE_FIXTURE = (
    REPOSITORY_ROOT
    / "tests"
    / "integrations"
    / "openai_agents"
    / "fixtures"
    / "combined_0_1_saved_state.json"
)
INSTALLED_PACKAGE_PROBE = (
    REPOSITORY_ROOT / "tests" / "integrations" / "openai_agents" / "installed_package_probe.py"
)
DEFAULT_OPENAI_AGENTS_TEST_VERSIONS = ("0.22.0", "0.22.2")


@dataclass(frozen=True, slots=True)
class DistributionArtifacts:
    wheel: Path
    sdist: Path
    wheel_from_sdist: Path


@dataclass(frozen=True, slots=True)
class BuiltArtifacts:
    core: DistributionArtifacts
    adapter: DistributionArtifacts


@dataclass(frozen=True, slots=True)
class AdapterEnvironment:
    python: Path
    sdk_version: str


def _load_pyproject(project_root: Path) -> dict[str, Any]:
    with (project_root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def _requirements(values: list[str]) -> dict[str, Requirement]:
    requirements = [Requirement(value) for value in values]
    return {canonicalize_name(requirement.name): requirement for requirement in requirements}


def _run(
    arguments: list[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["UV_NO_PROGRESS"] = "1"
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _require_success(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 0, (
        f"command failed with exit code {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def _single_artifact(directory: Path, pattern: str) -> Path:
    matches = tuple(directory.glob(pattern))
    assert len(matches) == 1, f"expected one {pattern} artifact, found {matches}"
    return matches[0]


def _build_distribution(
    *,
    uv: str,
    package: str,
    output: Path,
) -> DistributionArtifacts:
    output.mkdir(parents=True)
    completed = _run(
        [
            uv,
            "build",
            "--package",
            package,
            "--out-dir",
            str(output),
            "--no-sources",
        ],
        cwd=REPOSITORY_ROOT,
    )
    _require_success(completed)
    wheel = _single_artifact(output, "*.whl")
    sdist = _single_artifact(output, "*.tar.gz")

    rebuilt_output = output / "from-sdist"
    rebuilt_output.mkdir()
    rebuilt = _run(
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(rebuilt_output),
            "--no-sources",
            str(sdist),
        ],
        cwd=REPOSITORY_ROOT,
    )
    _require_success(rebuilt)
    return DistributionArtifacts(
        wheel=wheel,
        sdist=sdist,
        wheel_from_sdist=_single_artifact(rebuilt_output, "*.whl"),
    )


@pytest.fixture(scope="module")
def built_artifacts(tmp_path_factory: pytest.TempPathFactory) -> BuiltArtifacts:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required for built-artifact validation"
    output = tmp_path_factory.mktemp("distributions")
    return BuiltArtifacts(
        core=_build_distribution(
            uv=uv,
            package="mem-sandbox",
            output=output / "core",
        ),
        adapter=_build_distribution(
            uv=uv,
            package="mem-sandbox-openai-agents",
            output=output / "adapter",
        ),
    )


def _wheel_members(path: Path) -> frozenset[str]:
    with zipfile.ZipFile(path) as archive:
        return frozenset(name for name in archive.namelist() if not name.endswith("/"))


def _sdist_members(path: Path) -> frozenset[str]:
    with tarfile.open(path, mode="r:gz") as archive:
        members: set[str] = set()
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            assert len(parts) > 1
            members.add(PurePosixPath(*parts[1:]).as_posix())
        return frozenset(members)


def _wheel_metadata(path: Path) -> Message:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_names) == 1
        return BytesParser().parsebytes(archive.read(metadata_names[0]))


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _create_environment(uv: str, root: Path) -> Path:
    completed = _run(
        [uv, "venv", "--python", sys.executable, str(root)],
        cwd=root.parent,
    )
    _require_success(completed)
    python = _venv_python(root)
    assert python.is_file()
    return python


def _test_package_source_arguments() -> list[str]:
    wheelhouse = os.environ.get("MEM_SANDBOX_TEST_WHEELHOUSE")
    if wheelhouse is None:
        return []
    return ["--no-index", "--find-links", wheelhouse]


@pytest.fixture(scope="module")
def core_python(
    tmp_path_factory: pytest.TempPathFactory,
    built_artifacts: BuiltArtifacts,
) -> Path:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required for clean-install validation"
    root = tmp_path_factory.mktemp("clean-installs")

    core_python = _create_environment(uv, root / "core")
    core_install = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(core_python),
            "--no-index",
            str(built_artifacts.core.wheel_from_sdist),
        ],
        cwd=root,
    )
    _require_success(core_install)
    return core_python


def _requested_sdk_versions() -> tuple[str, ...]:
    configured = os.environ.get("MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS")
    if configured is None:
        return DEFAULT_OPENAI_AGENTS_TEST_VERSIONS
    versions = tuple(
        dict.fromkeys(value.strip() for value in configured.split(",") if value.strip())
    )
    if not versions:
        raise ValueError("MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS must name at least one version")
    return versions


@pytest.fixture(
    scope="module",
    params=_requested_sdk_versions(),
    ids=lambda version: f"openai-agents-{version}",
)
def adapter_environment(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    built_artifacts: BuiltArtifacts,
) -> AdapterEnvironment:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required for clean-install validation"
    sdk_version = cast(str, request.param)
    root = tmp_path_factory.mktemp(f"adapter-{sdk_version.replace('.', '-')}")
    adapter_python = _create_environment(uv, root / "environment")
    adapter_install = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(adapter_python),
            *_test_package_source_arguments(),
            f"openai-agents=={sdk_version}",
            "pydantic==2.12.2",
            str(built_artifacts.core.wheel_from_sdist),
            str(built_artifacts.adapter.wheel_from_sdist),
        ],
        cwd=root,
    )
    _require_success(adapter_install)
    pip_check = _run(
        [uv, "pip", "check", "--python", str(adapter_python)],
        cwd=root,
    )
    _require_success(pip_check)

    return AdapterEnvironment(python=adapter_python, sdk_version=sdk_version)


def test_uv_workspace_declares_the_adapter_member() -> None:
    pyproject = _load_pyproject(CORE_PROJECT_ROOT)
    with (CORE_PROJECT_ROOT / "uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)

    assert pyproject["tool"]["uv"]["workspace"]["members"] == ["packages/openai-agents"]
    assert lock["manifest"]["members"] == [
        "mem-sandbox",
        "mem-sandbox-openai-agents",
    ]


def test_core_metadata_is_dependency_free_and_removes_the_adapter_extra() -> None:
    project = _load_pyproject(CORE_PROJECT_ROOT)["project"]

    assert project["name"] == "mem-sandbox"
    assert project["version"] == "0.2.0"
    assert project["requires-python"] == ">=3.12"
    assert project["dependencies"] == []
    assert "optional-dependencies" not in project


def test_adapter_metadata_owns_its_version_and_compatibility_bounds() -> None:
    pyproject = _load_pyproject(ADAPTER_PROJECT_ROOT)
    project = pyproject["project"]
    requirements = _requirements(project["dependencies"])

    assert project["name"] == "mem-sandbox-openai-agents"
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.12"
    assert set(requirements) == {"mem-sandbox", "openai-agents", "pydantic"}
    assert requirements["mem-sandbox"].specifier == SpecifierSet(">=0.2.0,<0.3")
    assert requirements["openai-agents"].specifier == SpecifierSet(">=0.22.0,<0.23")
    assert requirements["pydantic"].specifier == SpecifierSet(">=2.12.2,<3")
    assert all(requirement.marker is None for requirement in requirements.values())
    assert pyproject["tool"]["uv"]["sources"]["mem-sandbox"] == {"workspace": True}


def test_source_roots_have_disjoint_namespace_ownership() -> None:
    assert CORE_PACKAGE_ROOT.is_dir()
    assert ADAPTER_PACKAGE_ROOT.is_dir()
    assert not LEGACY_ADAPTER_ROOT.exists()
    assert not (CORE_PROJECT_ROOT / "src" / "mem_sandbox_openai_agents").exists()
    assert not (ADAPTER_PROJECT_ROOT / "src" / "mem_sandbox").exists()


def test_each_distribution_owns_its_reader_license_and_typing_marker() -> None:
    assert (CORE_PROJECT_ROOT / "README.md").is_file()
    assert (CORE_PROJECT_ROOT / "LICENSE").is_file()
    assert (CORE_PACKAGE_ROOT / "py.typed").is_file()
    assert (ADAPTER_PROJECT_ROOT / "README.md").is_file()
    assert (ADAPTER_PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8").splitlines() == (
        CORE_PROJECT_ROOT / "LICENSE"
    ).read_text(encoding="utf-8").splitlines()
    assert (ADAPTER_PACKAGE_ROOT / "py.typed").is_file()


def test_release_workflow_routes_each_qualified_tag_to_one_package() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )

    assert "mem-sandbox-v*)" in workflow
    assert "mem-sandbox-openai-agents-v*)" in workflow
    assert 'package_version="$(uv version --package "${package}" --short)"' in workflow
    assert 'expected_tag="${package}-v${package_version}"' in workflow
    assert (
        'uv build --package "${{ steps.route.outputs.package }}" --out-dir dist --no-sources'
        in workflow
    )
    assert 'environment="pypi"' in workflow
    assert 'environment="pypi-openai-agents"' in workflow


def test_quality_workflow_syncs_and_builds_all_workspace_packages() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )

    assert "uv sync --locked --all-packages --all-groups" in workflow
    assert "uv build --all-packages --no-sources" in workflow
    assert "packages/openai-agents/src" in workflow
    assert 'MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS: "0.22.0,0.22.2"' in workflow


def test_adapter_guide_distinguishes_declared_and_exercised_sdk_support() -> None:
    guide = (ADAPTER_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`openai-agents>=0.22.0,<0.23`" in guide
    assert "| `0.22.0` | Supported lower bound |" in guide
    assert "| `0.22.2` | Supported exercised patch |" in guide
    assert "| Other `>=0.22.0,<0.23` versions | Declared but not individually exercised |" in guide
    assert "MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS" in guide
    for unsupported in (
        "host filesystem",
        "shell",
        "mount",
        "PTY",
        "port",
        "network",
        "compaction",
    ):
        assert unsupported in guide


def test_release_guide_requires_recurring_installed_package_compatibility_review() -> None:
    guide = (REPOSITORY_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS" in guide
    assert "tests/integrations/openai_agents/installed_package_probe.py" in guide
    assert "python -I" in guide
    assert "pip check" in guide
    assert "before widening the SDK upper bound" in guide


def test_default_installed_package_matrix_covers_lower_and_current_sdk_patch() -> None:
    assert DEFAULT_OPENAI_AGENTS_TEST_VERSIONS == ("0.22.0", "0.22.2")


def test_installed_package_probe_has_no_repository_only_imports() -> None:
    module = ast.parse(INSTALLED_PACKAGE_PROBE.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert not any(name == "samples" or name.startswith("samples.") for name in imported_modules)
    assert not any(name.startswith("tests.") for name in imported_modules)


def test_installed_package_probe_names_the_failing_sdk_contract_surface(tmp_path: Path) -> None:
    probe = tmp_path / "installed_package_probe.py"
    shutil.copyfile(INSTALLED_PACKAGE_PROBE, probe)

    completed = _run(
        [
            sys.executable,
            "-I",
            str(probe),
            "--expected-core-version",
            importlib.metadata.version("mem-sandbox"),
            "--expected-adapter-version",
            importlib.metadata.version("mem-sandbox-openai-agents"),
            "--expected-sdk-version",
            "0.0.0",
        ],
        cwd=tmp_path,
    )

    assert completed.returncode != 0
    assert (
        "OpenAI Agents SDK compatibility surface 'distribution versions' failed with AssertionError"
    ) in completed.stderr


def test_wheels_have_disjoint_namespace_ownership(built_artifacts: BuiltArtifacts) -> None:
    core_members = _wheel_members(built_artifacts.core.wheel)
    adapter_members = _wheel_members(built_artifacts.adapter.wheel)

    assert "mem_sandbox/py.typed" in core_members
    assert "mem_sandbox_openai_agents/py.typed" in adapter_members
    assert any(member.endswith(".dist-info/licenses/LICENSE") for member in core_members)
    assert any(member.endswith(".dist-info/licenses/LICENSE") for member in adapter_members)
    assert not any(member.startswith("mem_sandbox_openai_agents/") for member in core_members)
    assert not any(
        member.startswith("mem_sandbox/integrations/openai_agents/") for member in core_members
    )
    assert not any(member.startswith("mem_sandbox/") for member in adapter_members)
    assert core_members.isdisjoint(adapter_members)


def test_sdists_are_independent_and_rebuild_the_same_wheel_boundaries(
    built_artifacts: BuiltArtifacts,
) -> None:
    core_sdist = _sdist_members(built_artifacts.core.sdist)
    adapter_sdist = _sdist_members(built_artifacts.adapter.sdist)

    assert "pyproject.toml" in core_sdist
    assert "src/mem_sandbox/py.typed" in core_sdist
    assert not any("mem_sandbox_openai_agents" in member for member in core_sdist)
    assert "pyproject.toml" in adapter_sdist
    assert "README.md" in adapter_sdist
    assert "LICENSE" in adapter_sdist
    assert "src/mem_sandbox_openai_agents/py.typed" in adapter_sdist
    assert not any(member.startswith("src/mem_sandbox/") for member in adapter_sdist)
    assert _wheel_members(built_artifacts.core.wheel_from_sdist) == _wheel_members(
        built_artifacts.core.wheel
    )
    assert _wheel_members(built_artifacts.adapter.wheel_from_sdist) == _wheel_members(
        built_artifacts.adapter.wheel
    )


def test_built_metadata_matches_each_distribution_contract(
    built_artifacts: BuiltArtifacts,
) -> None:
    core = _wheel_metadata(built_artifacts.core.wheel_from_sdist)
    adapter = _wheel_metadata(built_artifacts.adapter.wheel_from_sdist)
    adapter_requirements = _requirements(adapter.get_all("Requires-Dist", []))

    assert core["Name"] == "mem-sandbox"
    assert core["Version"] == "0.2.0"
    assert core["Requires-Python"] == ">=3.12"
    assert core.get_all("Requires-Dist", []) == []
    assert core.get_all("Provides-Extra", []) == []
    assert "# MemSandbox" in core.get_payload()

    assert adapter["Name"] == "mem-sandbox-openai-agents"
    assert adapter["Version"] == "0.1.0"
    assert adapter["Requires-Python"] == ">=3.12"
    assert set(adapter_requirements) == {"mem-sandbox", "openai-agents", "pydantic"}
    assert adapter_requirements["mem-sandbox"].specifier == SpecifierSet(">=0.2.0,<0.3")
    assert adapter_requirements["openai-agents"].specifier == SpecifierSet(">=0.22.0,<0.23")
    assert adapter_requirements["pydantic"].specifier == SpecifierSet(">=2.12.2,<3")
    assert "# OpenAI Agents SDK Integration" in adapter.get_payload()


def test_core_sdist_wheel_installs_without_the_adapter_or_sdk(
    core_python: Path,
    tmp_path: Path,
) -> None:
    script = """
import asyncio
import importlib.util

from mem_sandbox import __version__
from mem_sandbox.workspace import AnyCurrentState, MemoryWorkspace, SandboxPath
from mem_sandbox.workspace import WorkspaceWriteRequest

async def exercise():
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/artifact.txt")
    await workspace.write(WorkspaceWriteRequest(path, b"core-only", AnyCurrentState()))
    assert (await workspace.read_bytes(path)).content == b"core-only"

assert __version__ == "0.2.0"
assert importlib.util.find_spec("agents") is None
assert importlib.util.find_spec("mem_sandbox_openai_agents") is None
asyncio.run(exercise())
"""
    completed = _run(
        [str(core_python), "-I", "-c", script],
        cwd=tmp_path,
    )

    _require_success(completed)


def test_adapter_sdist_wheel_passes_the_isolated_installed_package_probe(
    adapter_environment: AdapterEnvironment,
    tmp_path: Path,
) -> None:
    probe = tmp_path / "installed_package_probe.py"
    shutil.copyfile(INSTALLED_PACKAGE_PROBE, probe)
    completed = _run(
        [
            str(adapter_environment.python),
            "-I",
            str(probe),
            "--expected-core-version",
            "0.2.0",
            "--expected-adapter-version",
            "0.1.0",
            "--expected-sdk-version",
            adapter_environment.sdk_version,
        ],
        cwd=tmp_path,
    )

    _require_success(completed)
    assert json.loads(completed.stdout) == {
        "distributions": {
            "mem-sandbox": "0.2.0",
            "mem-sandbox-openai-agents": "0.1.0",
            "openai-agents": adapter_environment.sdk_version,
        },
        "isolated": True,
        "namespaces": {
            "adapter": "mem_sandbox_openai_agents.adapter",
            "legacy_namespace_available": False,
            "repository_samples_available": False,
        },
        "sdk_contract": {
            "document_review_outputs": [
                "Release draft corrected.",
                "Document review approved.",
            ],
            "replacement_resume": True,
            "runner_output": "Runner tool loop complete.",
            "state_type": "mem_sandbox",
            "tools": ["execute", "read_file", "write_file", "apply_patch"],
        },
        "surfaces": [
            "distribution versions",
            "public imports and namespace ownership",
            "client and session lifecycle",
            "capability binding and Runner tool loop",
            "snapshot state serialization and replacement resume",
            "document-review editor and reviewer workflow",
            "SDK session, backend, and service cleanup",
        ],
    }


def test_combined_0_1_saved_state_resumes_in_a_clean_adapter_install(
    adapter_environment: AdapterEnvironment,
    tmp_path: Path,
) -> None:
    script = """
from __future__ import annotations

import asyncio
import base64
import gzip
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import mem_sandbox_openai_agents as adapter
from agents.sandbox.session import SandboxSessionState
from mem_sandbox.core import Revision, SessionId, SnapshotId, SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SandboxSnapshotDraft,
    SnapshotMetadata,
    SnapshotStoreLimits,
)
from mem_sandbox.workspace import ContentHash

assert "mem_sandbox.integrations.openai_agents" not in sys.modules

async def exercise(fixture_path: str):
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    state_payload = fixture["state"]
    snapshot_fixture = fixture["snapshot"]
    expected = fixture["expected"]
    payload = gzip.decompress(base64.b64decode(snapshot_fixture["payload_gzip_base64"]))
    assert len(payload) == snapshot_fixture["metadata"]["payload_bytes"]

    clock = SystemClock()
    uuids = SystemUuidGenerator()
    codec = JsonSessionSnapshotCodec()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=2,
            max_total_payload_bytes=1024 * 1024,
        ),
        clock=clock,
    )
    await store.save(
        SandboxSnapshotDraft(
            snapshot_id=SnapshotId.parse(snapshot_fixture["snapshot_id"]),
            schema_version=snapshot_fixture["schema_version"],
            created_at=datetime.fromisoformat(snapshot_fixture["created_at"]),
            source_session_id=SessionId.parse(snapshot_fixture["source_session_id"]),
            workspace_revision=Revision(snapshot_fixture["workspace_revision"]),
            content_hash=ContentHash(snapshot_fixture["content_hash"]),
            payload=payload,
            metadata=SnapshotMetadata(
                format_name=snapshot_fixture["metadata"]["format_name"],
                payload_bytes=snapshot_fixture["metadata"]["payload_bytes"],
                process_local=snapshot_fixture["metadata"]["process_local"],
            ),
            created_by=snapshot_fixture["created_by"],
        )
    )
    factory = DefaultSessionFactory(
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(
            max_events=100,
            max_payload_bytes=1024 * 1024,
        ),
        snapshot_codec=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    service = InMemorySandboxService(
        session_factory=factory,
        snapshot_gateway=InMemoryServiceSnapshotGateway(store),
        snapshot_decoder=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    client = adapter.InMemorySandboxClient(
        service,
        snapshot_store=store,
        clock=clock,
    )

    registered = SandboxSessionState.parse(dict(state_payload))
    assert isinstance(registered, adapter.InMemorySandboxSessionState)
    assert registered.type == "mem_sandbox"
    assert registered.provider_state_version == 1
    assert registered.snapshot.type == "mem_sandbox_store"
    assert registered.snapshot.store_dependency_key == "mem_sandbox.snapshot_store"

    restored = client.deserialize_session_state(dict(state_payload))
    resumed = await client.resume(restored)
    await resumed.start()
    assert (await resumed.read(Path(expected["path"]))).read() == expected["content"].encode()
    await client.delete(resumed)
    await service.close()

asyncio.run(exercise(sys.argv[1]))
"""
    completed = _run(
        [
            str(adapter_environment.python),
            "-I",
            "-c",
            script,
            str(LEGACY_STATE_FIXTURE),
        ],
        cwd=tmp_path,
    )

    _require_success(completed)
