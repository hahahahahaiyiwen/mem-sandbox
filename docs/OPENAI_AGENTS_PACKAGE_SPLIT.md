# Core and OpenAI Agents Package Split

**Status:** Implemented and published for issue #67; compatibility maintenance continues
under issue #73

**Decision date:** 2026-09-10

**Applies to:** The first separately published core and OpenAI Agents SDK adapter
releases

## Decision summary

MemSandbox uses two independently versioned distributions in this repository:

| Distribution | Import namespace | First split version | Version owner | Release tag |
|---|---|---:|---|---|
| `mem-sandbox` | `mem_sandbox` | `0.2.0` | Root `pyproject.toml` | `mem-sandbox-v0.2.0` |
| `mem-sandbox-openai-agents` | `mem_sandbox_openai_agents` | `0.1.0` | `packages/openai-agents/pyproject.toml` | `mem-sandbox-openai-agents-v0.1.0` |

The core remains dependency-free. The adapter depends on public core contracts and the
SDK types it implements. Neither wheel owns files in the other distribution's import
namespace.

This is an explicit pre-1.0 breaking migration. Core `0.2.0` removes both the
`openai-agents` extra and `mem_sandbox.integrations.openai_agents`. There will be no
reverse-import shim, duplicate implementation, namespace overlap, or circular
compatibility extra.

## Distribution name

The selected adapter distribution name is **`mem-sandbox-openai-agents`**. Its import
package is **`mem_sandbox_openai_agents`**.

The name follows the
[Python package name normalization specification](https://packaging.python.org/en/latest/specifications/name-normalization/):
hyphens, underscores, and periods normalize to the same project name. Do not publish or
document a punctuation variant as a separate project.

On 2026-09-10, both of these canonical PyPI endpoints returned `404`, indicating that no
public project was registered under the normalized name at that time:

- `https://pypi.org/pypi/mem-sandbox-openai-agents/json`
- `https://pypi.org/simple/mem-sandbox-openai-agents/`

This check is not a reservation. PyPI documents that a
[pending Trusted Publisher does not reserve a name](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
until its first successful publication. Recheck the canonical endpoints immediately
before configuring the publisher and again before the first release. If another project
claims the name, stop and approve a replacement name before changing metadata or imports.
Issue #67 does not authorize project registration or publication.

## Package ownership

The repository layout is:

```text
pyproject.toml
src/
  mem_sandbox/
    ...
packages/
  openai-agents/
    pyproject.toml
    README.md
    LICENSE
    src/
      mem_sandbox_openai_agents/
        __init__.py
        adapter.py
        capability.py
        snapshot.py
        py.typed
```

`mem-sandbox` owns:

- the complete `mem_sandbox` namespace;
- workspace, command, session, service, event, policy, secret, and snapshot behavior;
- no required runtime dependencies;
- no OpenAI SDK imports, adapter implementation, adapter extra, or adapter compatibility
  module.

`mem-sandbox-openai-agents` owns:

- the complete `mem_sandbox_openai_agents` namespace;
- OpenAI-native client, session, state, snapshot, capability, and translation behavior;
- its own README, license copy, typing marker, metadata, version, artifacts, and release;
- imports of MemSandbox behavior through public `mem_sandbox` exports only.

Samples and conformance drivers may depend on both workspace members, but they remain
repository examples rather than installed modules in either distribution.

## Dependency bounds

The first adapter release publishes these runtime requirements:

```toml
requires-python = ">=3.12"
dependencies = [
  "mem-sandbox>=0.2.0,<0.3",
  "openai-agents>=0.22.0,<0.23",
  "pydantic>=2.12.2,<3",
]
```

| Dependency | Decision |
|---|---|
| `mem-sandbox>=0.2.0,<0.3` | `0.2.0` is the first core-only release. The `<0.3` bound prevents an unreviewed pre-1.0 core contract change from silently reaching the adapter. |
| `openai-agents>=0.22.0,<0.23` | Preserve the currently documented beta SDK range and exact `0.22.0` contract baseline. Expanding it requires explicit contract and conformance evidence. |
| `pydantic>=2.12.2,<3` | The adapter imports Pydantic directly, so it must declare Pydantic directly. The floor and major upper bound match the metadata of the exact `openai-agents==0.22.0` baseline. |
| Python `>=3.12` | Preserve the existing package and CI baseline; the split does not broaden Python compatibility. |

The core will continue to publish `dependencies = []`. Transitive availability through
`openai-agents` is not a substitute for the adapter's direct Pydantic declaration.

Required adapter validation retains `openai-agents==0.22.0` and
`pydantic==2.12.2` as the lower-bound pair. It also maintains an explicit matrix of SDK
patches exercised from installed artifacts; issue #73 adds `0.22.2`. Built-artifact tests
install the adapter with the built core artifact from the same commit. The normal locked
development environment or an unrecorded successful patch run does not expand either
declared range.

Core `0.3` or OpenAI Agents SDK `0.23` support requires a reviewed adapter release. A
core-only patch inside `0.2.x` or an adapter-only patch does not require synchronized
version numbers.

## Version and release ownership

Each distribution follows semantic-versioning intent independently:

- before `1.0`, a breaking public change increments that distribution's minor version;
- a compatible correction increments only that distribution's patch version;
- an adapter release does not mirror the core or SDK version;
- each `pyproject.toml` is the sole source of truth for its distribution version.

The historical `v0.1.0` tag remains the immutable combined-package release. Starting
with the split, generic `v<version>` tags are not used because two distributions may have
the same version.

Tag routing is exact:

| Tag pattern | Build context | Allowed artifacts |
|---|---|---|
| `mem-sandbox-v<version>` | Repository root | Only `mem-sandbox` wheel and sdist |
| `mem-sandbox-openai-agents-v<version>` | `packages/openai-agents` | Only `mem-sandbox-openai-agents` wheel and sdist |

A tag must match the selected package version exactly and point to a commit reachable
from `main`. One tag and GitHub release publish one distribution; the workflow must not
collect a shared `dist` directory or publish both packages opportunistically.

The existing protected `pypi` GitHub environment remains the core publisher boundary.
The adapter requires a separate `pypi-openai-agents` environment and a matching pending
Trusted Publisher for `mem-sandbox-openai-agents`. Configuring that publisher remains a
maintainer-approved release prerequisite, not part of moving source code.

Both publishers use `.github/workflows/publish.yml`, which will route the
distribution-qualified tag to one package:

| PyPI project | Workflow | GitHub environment |
|---|---|---|
| `mem-sandbox` | `publish.yml` | `pypi` |
| `mem-sandbox-openai-agents` | `publish.yml` | `pypi-openai-agents` |

When both packages change incompatibly, publish and verify the core first. Publish the
adapter only after its required core version is available from PyPI. PyPI publication is
not atomic:

- if core succeeds and adapter fails, leave the valid core release in place;
- correct the adapter and publish a new adapter version rather than replacing files;
- never publish adapter metadata that requires an unavailable core version;
- do not move or reuse an existing release tag.

## Pre-1.0 migration policy

The split is intentionally visible to users:

| Combined `mem-sandbox==0.1.x` usage | Split-package usage |
|---|---|
| `pip install "mem-sandbox[openai-agents]"` | `pip install mem-sandbox-openai-agents` |
| `from mem_sandbox.integrations.openai_agents import ...` | `from mem_sandbox_openai_agents import ...` |
| Core and adapter share one version | Core and adapter versions are independent |
| Generic `v<version>` release tag | Distribution-qualified release tag |

Core `0.2.0` will not retain the old extra. Some installers only warn when an
unrecognized extra is requested and may still install the core successfully. Migration
documentation must therefore state explicitly that
`pip install "mem-sandbox[openai-agents]"` no longer installs the adapter; clean
environment tests must use the new distribution and import.

Core `0.2.0` will not retain `mem_sandbox.integrations.openai_agents` as a forwarding
module. A shim would make the core import an external adapter or require duplicate files,
reintroducing the ownership and release coupling this split removes. Users migrate the
installation and import together.

The following serialized identifiers are not Python import paths and remain unchanged:

- provider type: `mem_sandbox`;
- snapshot type: `mem_sandbox_store`;
- dependency key: `mem_sandbox.snapshot_store`;
- existing provider-state and manifest profile versions;
- archive format: `openai-portable-workspace`.

Fresh-interpreter compatibility tests must import `mem_sandbox_openai_agents`, load
saved-state fixtures produced by the combined `0.1.x` implementation, deserialize them,
and exercise live reattachment or snapshot-backed resume as applicable. Register only
the new implementation in that process. Stable tags alone are insufficient evidence,
and Python pickle compatibility is not promised.

## Implementation evidence

Issue #67 implemented the split only after the target package tests failed against the
combined layout. The repository now:

1. builds both workspace members directly and independently from their sdists;
2. inspects wheel and sdist ownership plus published dependency metadata;
3. installs the core wheel alone in a clean environment and exercises a model-free
   workspace lifecycle;
4. installs adapter and core wheels at the SDK/Pydantic baseline, runs `pip check`, and
   imports only the new namespace outside the checkout;
5. loads a checked-in combined-`0.1.0` state and durable snapshot fixture in a fresh
   interpreter, verifies SDK type registration, and performs snapshot-backed resume;
6. runs package-boundary guards, conformance, samples, benchmarks, and quality checks
   against both workspace members.

The implementation updates imports, source-root checks, Ruff/Pyright inputs, CI, and
release routing in the same migration. Publication and Trusted Publisher configuration
remain explicitly outside issue #67.

Issue #73 extends this evidence without changing package boundaries or dependency
ranges. A standalone probe copied outside the checkout now checks exact distribution
versions, public namespace ownership, dependency consistency, client/session lifecycle,
the four-tool SDK `Runner` loop, JSON-safe snapshot state, replacement resume, the
deterministic document-review editor/reviewer workflow, and cleanup. The default CI
matrix exercises SDK `0.22.0` and `0.22.2`; other patches inside the declared range
remain declared but not individually exercised until the matrix and adapter README are
updated.
