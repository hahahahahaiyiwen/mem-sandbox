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

OPENAI_EXTRA = "openai-agents"
SUPPORTED_OPENAI_AGENTS_RANGE = SpecifierSet(">=0.22,<0.23")
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
OPENAI_INTEGRATION_MODULE = "mem_sandbox.integrations.openai_agents"


def _public_exports(module_name: str) -> frozenset[str]:
    if module_name == "mem_sandbox":
        return frozenset({"__version__"})

    module = importlib.import_module(module_name)
    return frozenset(cast(list[str], module.__dict__["__all__"]))


def _private_core_imports(path: Path, *, package_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name.startswith("mem_sandbox")
                    and not alias.name.startswith(OPENAI_INTEGRATION_MODULE)
                    and alias.name not in PUBLIC_CORE_MODULES
                ):
                    violations.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                relative_name = "." * node.level + (node.module or "")
                module_name = resolve_name(relative_name, package_name)
            else:
                module_name = node.module

            if module_name is None or not module_name.startswith("mem_sandbox"):
                continue
            if module_name.startswith(OPENAI_INTEGRATION_MODULE):
                continue
            if module_name not in PUBLIC_CORE_MODULES:
                violations.add(module_name)
                continue

            public_exports = _public_exports(module_name)
            violations.update(
                f"{module_name}:{alias.name}"
                for alias in node.names
                if alias.name == "*" or alias.name not in public_exports
            )

    return violations


def _package_name(path: Path, integration_root: Path) -> str:
    relative_parent = path.relative_to(integration_root).parent
    suffix = ".".join(relative_parent.parts)
    return OPENAI_INTEGRATION_MODULE if not suffix else f"{OPENAI_INTEGRATION_MODULE}.{suffix}"


def test_openai_agents_extra_uses_the_supported_sdk_range() -> None:
    requirements = [
        Requirement(requirement) for requirement in importlib.metadata.requires("mem-sandbox") or ()
    ]
    openai_requirements = [
        requirement for requirement in requirements if requirement.name == "openai-agents"
    ]

    assert len(openai_requirements) == 1
    requirement = openai_requirements[0]
    assert requirement.specifier == SUPPORTED_OPENAI_AGENTS_RANGE
    assert requirement.marker is not None
    assert requirement.marker.evaluate({"extra": OPENAI_EXTRA})
    assert not requirement.marker.evaluate({"extra": ""})


def test_openai_agents_integration_package_exists() -> None:
    integration = importlib.import_module(OPENAI_INTEGRATION_MODULE)

    assert integration.__name__ == OPENAI_INTEGRATION_MODULE


@pytest.mark.parametrize(
    ("source", "expected_violation"),
    [
        (
            "from ...session.session import SandboxSession\n",
            "mem_sandbox.session.session",
        ),
        (
            "from mem_sandbox.session import session\n",
            "mem_sandbox.session:session",
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


def test_public_core_import_guard_accepts_public_relative_import(tmp_path: Path) -> None:
    module = tmp_path / "adapter.py"
    module.write_text("from ...session import SandboxSession\n", encoding="utf-8")

    assert (
        _private_core_imports(
            module,
            package_name=OPENAI_INTEGRATION_MODULE,
        )
        == set()
    )


def test_openai_adapter_uses_only_public_core_module_exports() -> None:
    repository_root = Path(__file__).parents[3]
    integration_root = repository_root / "src" / "mem_sandbox" / "integrations" / "openai_agents"
    violations: dict[str, list[str]] = {}

    for path in integration_root.rglob("*.py"):
        private_imports = _private_core_imports(
            path,
            package_name=_package_name(path, integration_root),
        )
        if private_imports:
            violations[str(path.relative_to(repository_root))] = sorted(private_imports)

    assert violations == {}
