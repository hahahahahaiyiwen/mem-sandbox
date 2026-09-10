from __future__ import annotations

import ast
import importlib.metadata
import subprocess
import sys
from pathlib import Path

from mem_sandbox import __version__

ALLOWED_CORE_IMPORT_ROOTS = frozenset(sys.stdlib_module_names) | {"mem_sandbox"}


def _absolute_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            roots.add(node.module.partition(".")[0])

    return roots


def _unexpected_core_import_roots(path: Path) -> set[str]:
    return _absolute_import_roots(path) - ALLOWED_CORE_IMPORT_ROOTS


def test_package_imports_in_an_isolated_interpreter() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-c", "import mem_sandbox"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_importing_core_does_not_import_openai_agents() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; import mem_sandbox; assert 'agents' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_package_imports_when_openai_agents_is_unavailable() -> None:
    script = """
import builtins

original_import = builtins.__import__

def import_without_openai_agents(name, *args, **kwargs):
    if name == "agents" or name.startswith("agents."):
        raise ModuleNotFoundError(name)
    return original_import(name, *args, **kwargs)

builtins.__import__ = import_without_openai_agents
import mem_sandbox
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_distribution_has_no_required_runtime_dependencies() -> None:
    requirements = importlib.metadata.requires("mem-sandbox") or []
    unconditional = [requirement for requirement in requirements if "extra ==" not in requirement]

    assert unconditional == []


def test_distribution_metadata_matches_the_public_package() -> None:
    metadata = importlib.metadata.metadata("mem-sandbox")

    assert metadata["Name"] == "mem-sandbox"
    assert metadata["Version"] == __version__
    assert metadata["Requires-Python"] == ">=3.12"


def test_import_guard_detects_any_third_party_package(tmp_path: Path) -> None:
    source = tmp_path / "third_party_import.py"
    source.write_text("import crewai\n", encoding="utf-8")

    assert _unexpected_core_import_roots(source) == {"crewai"}


def test_core_modules_import_only_stdlib_and_mem_sandbox() -> None:
    repository_root = Path(__file__).parents[3]
    package_root = repository_root / "src" / "mem_sandbox"

    violations: dict[str, list[str]] = {}
    for path in package_root.rglob("*.py"):
        unexpected = _unexpected_core_import_roots(path)
        if unexpected:
            violations[str(path.relative_to(repository_root))] = sorted(unexpected)

    assert violations == {}
