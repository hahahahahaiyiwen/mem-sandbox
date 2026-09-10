# MemSandbox

A deterministic in-memory workspace and constrained command environment for AI agents.

MemSandbox lets an application give an agent stateful files, familiar POSIX-shaped
commands, snapshots, policy enforcement, and explicit lifecycle management without
giving the model direct access to the host filesystem or shell.

> **Status:** The framework-neutral core and first native OpenAI Agents SDK
> integration are implemented and tested on Python 3.12 and 3.14 for Linux and Windows.
> APIs may still change before the first stable release.

## Quick start

### Install from PyPI

MemSandbox requires Python 3.12 or later. Install the framework-neutral core:

```console
python -m pip install mem-sandbox
```

Install the OpenAI Agents SDK adapter and its supported SDK version:

```console
python -m pip install "mem-sandbox[openai-agents]"
```

Verify the installed distribution:

```console
python -c "from importlib.metadata import version; print(version('mem-sandbox'))"
```

The core package has no runtime dependencies. The `openai-agents` extra currently
selects `openai-agents>=0.22,<0.23`.

### Exercise the installed package

Save this as `quickstart.py`:

```python
import asyncio

from mem_sandbox.workspace import (
    MemoryWorkspace,
    PathMustNotExist,
    WorkspaceWriteRequest,
)


async def main() -> None:
    workspace = MemoryWorkspace()
    path = workspace.resolve_path("/workspace/demo/message.txt")
    await workspace.write(
        WorkspaceWriteRequest(
            path=path,
            content=b"hello from MemSandbox\n",
            precondition=PathMustNotExist(),
            create_parents=True,
        )
    )
    result = await workspace.read_text(path)
    print(result.content, end="")


asyncio.run(main())
```

Run it:

```console
python quickstart.py
```

```text
hello from MemSandbox
```

The example uses only public types from the installed wheel and never touches the host
filesystem. Low-level `MemoryWorkspace` paths are absolute and rooted at `/workspace`;
sessions add their own working-directory behavior.

To give an OpenAI Agents SDK `SandboxAgent` the four model-facing tools, follow the
complete
[`SandboxAgent` package usage path](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/src/mem_sandbox/integrations/openai_agents/README.md#use-with-sandboxagent).

### Run the repository samples

The maintained command-line and live-model samples are repository source examples; they
are not installed by the wheel. To run them, install
[Git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/), then clone the
repository and install its optional integration dependencies:

```console
git clone https://github.com/hahahahahaiyiwen/mem-sandbox.git
cd mem-sandbox
uv sync --all-groups
```

#### 1. Explore MemSandbox without a model

```console
uv run python -m samples.shared
```

This opens an empty, process-local sandbox with no credentials, model, or network
request:

```text
mem-sandbox:/workspace> pwd
/workspace
mem-sandbox:/workspace> mkdir demo
mem-sandbox:/workspace> echo hello > demo/message.txt
mem-sandbox:/workspace> cat demo/message.txt
hello
mem-sandbox:/workspace> exit
```

Commands run in MemSandbox's constrained virtual command language, not a host shell.
The in-memory session is deleted when the CLI exits.

#### 2. Run the `workspace-edit` agent scenario

Choose one inference provider.

**Official OpenAI**

```sh
export OPENAI_API_KEY="<api-key>"
export OPENAI_MODEL="<model>"

uv run python -m samples.openai_agents_sdk.providers.openai \
  --scenario workspace-edit \
  --inspect
```

**Azure OpenAI**

```sh
export AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com"
export AZURE_OPENAI_API_KEY="<api-key>"
export AZURE_OPENAI_API_VERSION="<api-version>"
export AZURE_OPENAI_DEPLOYMENT="<deployment-name>"

uv run python -m samples.openai_agents_sdk.providers.azure_openai \
  --scenario workspace-edit \
  --inspect
```

The scenario asks the agent to create a file, read its content hash, apply a guarded
patch from `status=pending` to `status=complete`, and read the result. Host code verifies
the exact final bytes before `--inspect` attaches the constrained CLI to the same live
session:

```text
mem-sandbox:/workspace> cat /workspace/demo/report.txt
status=complete
mem-sandbox:/workspace> exit
```

Live runs are billable and require network access. Credentials are read from the process
environment and are never placed in the sandbox. For provider requirements, see the
[official OpenAI guide](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/samples/openai_agents_sdk/providers/openai/README.md)
or
[Azure OpenAI guide](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/samples/openai_agents_sdk/providers/azure_openai/README.md).
To embed the integration in an application instead of running the repository scenario,
follow the complete
[`SandboxAgent` usage path](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/src/mem_sandbox/integrations/openai_agents/README.md#use-with-sandboxagent).

List the other available scenarios without credentials or a network request:

```console
uv run python -m samples.openai_agents_sdk.providers.openai --list-scenarios
```

## How the agent integration works

```text
Official OpenAI or Azure OpenAI
    |
    v
OpenAI Agents SDK SandboxAgent
    |
    v
InMemorySandboxCapability
    |
    v
MemSandbox session
    +-- in-memory POSIX workspace
    +-- constrained command executor
    +-- policy, quotas, events, and snapshots
    |
    +-- host verification and optional inspection CLI
```

The model receives exactly four tools: `execute`, `read_file`, `write_file`, and
`apply_patch`. It does not receive a sandbox handle, lifecycle controls, snapshot
authority, secrets, mounts, ports, network access, a host shell, or arbitrary process
execution.

Choose the documentation path that matches the task:

- **Run a maintained live example:** use the
  [OpenAI Agents SDK samples](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/samples/openai_agents_sdk/README.md).
- **Embed the public interfaces:** use the
  [reader-first OpenAI integration guide](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/src/mem_sandbox/integrations/openai_agents/README.md#use-with-sandboxagent).
- **Review adapter invariants:** use the integration guide's
  [engineering reference](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/src/mem_sandbox/integrations/openai_agents/README.md#engineering-reference).

## What MemSandbox provides

- **Stateful virtual workspace:** byte-oriented files and directories with canonical
  POSIX paths, atomic mutations, content hashes, and configurable quotas.
- **Constrained command environment:** deterministic implementations of selected
  familiar commands, including pipelines, conditionals, environment changes, and
  redirection.
- **Lifecycle service:** create, look up, resume, and delete isolated process-local
  sessions through opaque host-owned handles.
- **Portable state:** bounded workspace archives, snapshots, resume, and isolated forks.
- **Host-owned controls:** policy, secrets, events, limits, manifests, and capability
  selection remain application responsibilities.
- **Framework-neutral core:** agent SDK packages are optional adapters over the same
  domain API.

The implemented command profile includes `pwd`, `cd`, `ls`, `cat`, `echo`, `mkdir`,
`touch`, `rm`, `head`, `tail`, `grep`, `find`, `wc`, `sort`, `uniq`, `cp`, `mv`, `env`,
`export`, and `unset`. This is an explicit virtual command language, not a complete shell.

## Security boundary

MemSandbox is a **logical and API sandbox**, not an operating-system isolation boundary.
Supported operations do not fall through to the host filesystem or shell, but arbitrary
Python or native code running in the same process is outside its containment model.

Use a process, container, VM, microVM, or WASM boundary when executing untrusted
arbitrary code. Network access, host subprocesses, mounts, PTYs, and full POSIX shell
compatibility are not part of the current profile.

Read the
[high-level design](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/HIGH_LEVEL_DESIGN.md)
and
[OpenAI integration boundary](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/src/mem_sandbox/integrations/openai_agents/README.md)
before embedding MemSandbox in a security-sensitive application.

## Project status and documentation

Milestone 5 delivered the first native integration through
`openai-agents>=0.22,<0.23`, tested exactly with `0.22.0`. Other framework and transport
integrations remain non-gating follow-ups.

- [Design and research index](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/README.md)
- [High-level design](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/HIGH_LEVEL_DESIGN.md)
- [Implementation plan](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/PLAN.md)
- [Run samples and configure providers](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/samples/README.md)
- [Embed or maintain the OpenAI Agents SDK integration](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/src/mem_sandbox/integrations/openai_agents/README.md)
- [Workspace design](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/components/workspace/README.md)
- [Command executor design](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/components/command-executor/README.md)
- [Product validation and benchmarks](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/docs/product-validation/README.md)

## Development

```console
uv sync --all-groups
uv run pytest
uv run ruff format --check benchmarks evaluations samples src tests
uv run ruff check benchmarks evaluations samples src tests
uv run pyright benchmarks evaluations samples src tests
uv build
```

Development uses short-lived issue branches in separate Git worktrees. The main checkout
stays on `main`, and changes reach it through pull requests.

## Contributing, security, and license

See
[CONTRIBUTING.md](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/CONTRIBUTING.md)
before starting a change. Report security issues using the private process in
[SECURITY.md](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/SECURITY.md),
not a public issue.

MemSandbox is licensed under the
[MIT License](https://github.com/hahahahahaiyiwen/mem-sandbox/blob/main/LICENSE).
