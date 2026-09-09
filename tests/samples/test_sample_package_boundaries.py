from __future__ import annotations

import ast
from pathlib import Path

_SHARED_ROOT = Path(__file__).parents[2] / "samples" / "shared"
_FORBIDDEN_SHARED_IMPORT_ROOTS = frozenset({"agents", "openai"})


def _absolute_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            roots.add(node.module.partition(".")[0])
    return roots


def test_shared_sample_utilities_do_not_import_agent_or_provider_sdks() -> None:
    violations = {
        path.relative_to(_SHARED_ROOT).as_posix(): sorted(
            _absolute_import_roots(path) & _FORBIDDEN_SHARED_IMPORT_ROOTS
        )
        for path in sorted(_SHARED_ROOT.rglob("*.py"))
        if _absolute_import_roots(path) & _FORBIDDEN_SHARED_IMPORT_ROOTS
    }

    assert violations == {}
