from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from mem_sandbox.service import InMemorySandboxService

_ROOT = Path(__file__).resolve().parents[1]
_PYTHON_BLOCK = re.compile(r"^```python\n(.*?)\n```$", re.MULTILINE | re.DOTALL)
_MARKDOWN_LINK = re.compile(r"\]\(([^)]+)\)")
_REPOSITORY_BLOB = "https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/"
_DISTRIBUTED_READMES = (
    _ROOT / "README.md",
    _ROOT / "src/mem_sandbox/integrations/openai_agents/README.md",
)


def _section(path: Path, start: str, end: str) -> str:
    content = path.read_text(encoding="utf-8")
    section_start = content.index(start)
    section_end = content.index(end, section_start)
    return content[section_start:section_end]


def test_root_readme_installed_package_quickstart(
    capsys: pytest.CaptureFixture[str],
) -> None:
    section = _section(
        _ROOT / "README.md",
        "### Exercise the installed package",
        "### Run the repository samples",
    )
    blocks = _PYTHON_BLOCK.findall(section)

    assert len(blocks) == 1
    exec(compile(blocks[0], "README.md", "exec"), {"__name__": "readme_quickstart"})

    captured = capsys.readouterr()
    assert captured.out == "hello from MemSandbox\n"


def test_openai_readme_public_composition_constructs_and_closes() -> None:
    section = _section(
        _ROOT / "src/mem_sandbox/integrations/openai_agents/README.md",
        "## Use with `SandboxAgent`",
        "### Model-facing tools and common limits",
    )
    blocks = _PYTHON_BLOCK.findall(section)
    namespace: dict[str, object] = {"__name__": "openai_integration_readme"}

    assert len(blocks) == 3
    for block in blocks:
        exec(
            compile(block, "src/mem_sandbox/integrations/openai_agents/README.md", "exec"),
            namespace,
        )

    create_service = cast(
        Callable[[], InMemorySandboxService],
        namespace["create_sandbox_service"],
    )
    service = create_service()
    asyncio.run(service.close())


def test_distributed_readme_links_are_portable_and_resolve_in_repository() -> None:
    for readme in _DISTRIBUTED_READMES:
        targets = _MARKDOWN_LINK.findall(readme.read_text(encoding="utf-8"))

        relative_targets = [target for target in targets if target.startswith(("./", "../"))]
        assert relative_targets == []

        for target in targets:
            if not target.startswith(_REPOSITORY_BLOB):
                continue
            repository_path = target.removeprefix(_REPOSITORY_BLOB).split("#", 1)[0]
            assert (_ROOT / repository_path).is_file(), target
