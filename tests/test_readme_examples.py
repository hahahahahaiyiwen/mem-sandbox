from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from agents.models.interface import Model
from samples.shared.service import create_sample_service

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


class _ReadmeRunResult:
    final_output: object = "done"


class _ReadmeRunner:
    @staticmethod
    async def run(*args: object, **kwargs: object) -> _ReadmeRunResult:
        assert len(args) == 2
        assert "run_config" in kwargs
        return _ReadmeRunResult()


def test_root_readme_installed_package_quickstart() -> None:
    section = _section(
        _ROOT / "README.md",
        "### Exercise the installed package",
        "### Develop and run the repository samples",
    )
    blocks = _PYTHON_BLOCK.findall(section)
    namespace: dict[str, object] = {"__name__": "readme_quickstart"}

    assert len(blocks) == 1
    exec(compile(blocks[0], "README.md", "exec"), namespace)
    namespace["Runner"] = _ReadmeRunner
    run_sandbox_agent = cast(
        Callable[..., Awaitable[object]],
        namespace["run_sandbox_agent"],
    )

    async def exercise() -> None:
        service = create_sample_service()
        try:
            result = await run_sandbox_agent(
                model=cast(Model, "unused-model"),
                service=service,
            )
            assert result == "done"
        finally:
            await service.close()

    asyncio.run(exercise())


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
