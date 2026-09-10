from __future__ import annotations

import ast
import importlib
import importlib.metadata
from importlib.util import resolve_name
from pathlib import Path
from typing import cast

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

CORE_DISTRIBUTION = "mem-sandbox"
OPENAI_ADAPTER_DISTRIBUTION = "mem-sandbox-openai-agents"
SUPPORTED_CORE_RANGE = SpecifierSet(">=0.2.0,<0.3")
SUPPORTED_OPENAI_AGENTS_RANGE = SpecifierSet(">=0.22,<0.23")
SUPPORTED_PYDANTIC_RANGE = SpecifierSet(">=2.12.2,<3")
PUBLIC_CORE_MODULES = frozenset(
    {
        "mem_sandbox",
        "mem_sandbox.command_executor",
        "mem_sandbox.core",
        "mem_sandbox.events",
        "mem_sandbox.policy",
        "mem_sandbox.secrets",
        "mem_sandbox.service",
        "mem_sandbox.session",
        "mem_sandbox.snapshots",
        "mem_sandbox.workspace",
    }
)
OPENAI_INTEGRATION_MODULE = "mem_sandbox_openai_agents"
FORBIDDEN_PUBLIC_CORE_IMPORTS = frozenset(
    {
        ("mem_sandbox.workspace", "JsonWorkspaceSnapshotCodec"),
        ("mem_sandbox.workspace", "MemoryWorkspace"),
        ("mem_sandbox.workspace", "PortableWorkspaceArchiveCodec"),
        ("mem_sandbox.workspace", "WorkspaceArchivePort"),
    }
)


def _public_exports(module_name: str) -> frozenset[str]:
    if module_name == "mem_sandbox":
        return frozenset({"__version__"})

    module = importlib.import_module(module_name)
    return frozenset(cast(list[str], module.__dict__["__all__"]))


def _is_core_module(module_name: str) -> bool:
    return module_name == "mem_sandbox" or module_name.startswith("mem_sandbox.")


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def _private_core_imports(path: Path, *, package_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: set[str] = set()
    module_aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None and alias.name in PUBLIC_CORE_MODULES:
                    module_aliases[alias.asname] = alias.name
                if _is_core_module(alias.name) and alias.name not in PUBLIC_CORE_MODULES:
                    violations.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                relative_name = "." * node.level + (node.module or "")
                module_name = resolve_name(relative_name, package_name)
            else:
                module_name = node.module

            if module_name is None or not _is_core_module(module_name):
                continue
            if module_name not in PUBLIC_CORE_MODULES:
                violations.add(module_name)
                continue

            public_exports = _public_exports(module_name)
            violations.update(
                f"{module_name}:{alias.name}"
                for alias in node.names
                if (
                    alias.name == "*"
                    or alias.name not in public_exports
                    or (module_name, alias.name) in FORBIDDEN_PUBLIC_CORE_IMPORTS
                )
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        qualified_name = _qualified_name(node)
        if qualified_name is None:
            continue
        first, separator, remainder = qualified_name.partition(".")
        if separator and first in module_aliases:
            qualified_name = f"{module_aliases[first]}.{remainder}"
        for module_name in sorted(PUBLIC_CORE_MODULES, key=len, reverse=True):
            prefix = f"{module_name}."
            if not qualified_name.startswith(prefix):
                continue
            public_name = qualified_name.removeprefix(prefix).partition(".")[0]
            if public_name not in _public_exports(module_name):
                violations.add(f"{module_name}:{public_name}")
            break
        violations.update(
            f"{module_name}:{symbol}"
            for module_name, symbol in FORBIDDEN_PUBLIC_CORE_IMPORTS
            if qualified_name == f"{module_name}.{symbol}"
        )

    return violations


def _package_name(path: Path, integration_root: Path) -> str:
    relative_parent = path.relative_to(integration_root).parent
    suffix = ".".join(relative_parent.parts)
    return OPENAI_INTEGRATION_MODULE if not suffix else f"{OPENAI_INTEGRATION_MODULE}.{suffix}"


def test_adapter_distribution_declares_supported_runtime_ranges() -> None:
    requirements = [
        Requirement(requirement)
        for requirement in importlib.metadata.requires(OPENAI_ADAPTER_DISTRIBUTION) or ()
    ]
    by_name = {requirement.name: requirement for requirement in requirements}

    assert set(by_name) == {CORE_DISTRIBUTION, "openai-agents", "pydantic"}
    assert by_name[CORE_DISTRIBUTION].specifier == SUPPORTED_CORE_RANGE
    assert by_name["openai-agents"].specifier == SUPPORTED_OPENAI_AGENTS_RANGE
    assert by_name["pydantic"].specifier == SUPPORTED_PYDANTIC_RANGE
    assert all(requirement.marker is None for requirement in requirements)


def test_openai_agents_integration_package_exists() -> None:
    integration = importlib.import_module(OPENAI_INTEGRATION_MODULE)

    assert integration.__name__ == OPENAI_INTEGRATION_MODULE


@pytest.mark.parametrize(
    ("source", "expected_violation"),
    [
        (
            "from mem_sandbox.session.session import SandboxSession\n",
            "mem_sandbox.session.session",
        ),
        (
            "from mem_sandbox.session import session\n",
            "mem_sandbox.session:session",
        ),
        (
            "from mem_sandbox.workspace import MemoryWorkspace\n",
            "mem_sandbox.workspace:MemoryWorkspace",
        ),
        (
            "import mem_sandbox.workspace as workspace\nworkspace.MemoryWorkspace()\n",
            "mem_sandbox.workspace:MemoryWorkspace",
        ),
        (
            "import mem_sandbox.workspace as workspace\nworkspace.memory.MemoryWorkspace()\n",
            "mem_sandbox.workspace:memory",
        ),
    ],
)
def test_public_core_import_guard_rejects_private_imports(
    tmp_path: Path,
    source: str,
    expected_violation: str,
) -> None:
    module = tmp_path / "adapter.py"
    module.write_text(source, encoding="utf-8")

    assert _private_core_imports(
        module,
        package_name=OPENAI_INTEGRATION_MODULE,
    ) == {expected_violation}


def test_public_core_import_guard_accepts_adapter_relative_import(tmp_path: Path) -> None:
    module = tmp_path / "adapter.py"
    module.write_text(
        "from .snapshot import InMemorySandboxSnapshot\n",
        encoding="utf-8",
    )

    assert (
        _private_core_imports(
            module,
            package_name=OPENAI_INTEGRATION_MODULE,
        )
        == set()
    )


@pytest.mark.parametrize(
    "symbol",
    [
        "JsonWorkspaceSnapshotCodec",
        "PortableWorkspaceArchiveCodec",
        "WorkspaceArchivePort",
    ],
)
def test_public_core_import_guard_rejects_concrete_archive_boundaries(
    tmp_path: Path,
    symbol: str,
) -> None:
    module = tmp_path / "adapter.py"
    module.write_text(
        f"from mem_sandbox.workspace import {symbol}\n",
        encoding="utf-8",
    )

    assert _private_core_imports(
        module,
        package_name=OPENAI_INTEGRATION_MODULE,
    ) == {f"mem_sandbox.workspace:{symbol}"}


def test_openai_adapter_uses_only_public_core_module_exports() -> None:
    repository_root = Path(__file__).parents[3]
    integration_root = (
        repository_root / "packages" / "openai-agents" / "src" / "mem_sandbox_openai_agents"
    )
    violations: dict[str, list[str]] = {}

    for path in integration_root.rglob("*.py"):
        private_imports = _private_core_imports(
            path,
            package_name=_package_name(path, integration_root),
        )
        if private_imports:
            violations[str(path.relative_to(repository_root))] = sorted(private_imports)

    assert violations == {}
