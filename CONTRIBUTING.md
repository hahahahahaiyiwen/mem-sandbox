# Contributing to MemSandbox

Thank you for helping build MemSandbox.

## Before starting

1. Open or select a GitHub issue describing the behavior and acceptance criteria.
2. Keep the main checkout on `main`.
3. Create a short-lived issue branch in a separate worktree:

   ```console
   git fetch origin
   git worktree add ../mem-sandbox-<short-name> -b feature/<issue>-<short-name> main
   ```

Use `fix/<issue>-<short-name>` for bug fixes. Do not implement directly in the main
checkout.

## Development setup

Install Python 3.12 or later and [uv](https://docs.astral.sh/uv/), then run:

```console
uv sync --all-packages --all-groups
```

## Implementation expectations

- Write or update behavior tests before implementation.
- Keep core modules independent of agent frameworks.
- Express collaborators through narrow constructor-injected interfaces.
- Use immutable domain request and result types at boundaries.
- Reject unsupported behavior explicitly; never fall back to the host filesystem or shell.
- Update the owning module `README.md` when behavior, interfaces, invariants, or
  dependencies change.

## Required checks

Run the same checks used by CI:

```console
uv run pytest
uv run ruff format --check packages src tests
uv run ruff check packages src tests
uv run pyright packages/openai-agents/src src tests
uv build --all-packages
```

## Pull requests

Keep each pull request focused on one issue or behavior seam. Explain the behavior change,
important design decisions, and any deferred work. Link the issue and ensure all required
checks pass before merge.

## Publishing a release

Publishing is maintainer-only and uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) rather than a
long-lived repository token. Each PyPI project has an isolated publisher boundary:

| Project | Version source | Release tag | GitHub environment |
|---|---|---|---|
| `mem-sandbox` | `pyproject.toml` | `mem-sandbox-v<version>` | `pypi` |
| `mem-sandbox-openai-agents` | `packages/openai-agents/pyproject.toml` | `mem-sandbox-openai-agents-v<version>` | `pypi-openai-agents` |

Both Trusted Publishers use repository `hahahahahaiyiwen/mem-sandbox` and workflow
`publish.yml`. Each protected environment should require approval. The publish job
receives only `contents: read` and `id-token: write`; the separate build job cannot
request an OIDC token. A pending adapter publisher must be configured and the canonical
PyPI name rechecked before its first release.

### OpenAI Agents SDK compatibility gate

Every adapter release retains the exact SDK lower bound and reruns each patch listed in
the adapter README's exercised-version matrix. The default command builds both
distributions, installs them without editable or checkout imports, runs `pip check`, and
executes the model-free SDK and document-review probe:

```console
uv run pytest tests/integrations/openai_agents/test_distribution_artifacts.py
```

To evaluate a new patch, set `MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS` to a
comma-separated list containing `0.22.0` and the candidate. If it passes, add the version
to the default test constant, the Quality workflow value, and the adapter compatibility
matrix in the same change. Review SDK abstract methods, signatures, state/run
configuration, lifecycle, serialization, capability binding, and snapshot changes
before widening the SDK upper bound; do not infer wider support from dependency
resolution alone.

To publish:

1. Merge the release changes and a unique semantic version in the selected package's
   `pyproject.toml` to `main`.
2. Confirm the required `main` checks pass.
3. Create and push the selected distribution-qualified tag at the intended `main`
   commit.
4. Publish a GitHub release for that tag.
5. Approve the selected environment deployment after reviewing its isolated artifacts.
6. Install the exact version from PyPI in a clean environment, run `pip check`, and
   verify its public imports and supported SDK version.

For the post-publication adapter check, copy
`tests/integrations/openai_agents/installed_package_probe.py` to a temporary directory
outside the checkout and run it with the environment's Python:

```console
python -I installed_package_probe.py \
  --expected-core-version <core-version> \
  --expected-adapter-version <adapter-version> \
  --expected-sdk-version <sdk-version>
```

Run this once for every exercised SDK row. Preserve its JSON output with the release
evidence. The probe performs no model-network calls; a package-CDN download failure is a
retrieval failure and must not be reported as SDK incompatibility.

The release workflow rejects a tag that does not exactly match the selected package
version and publishes only that package's wheel and source distribution. When both
packages change incompatibly, publish and verify core first. PyPI publication is not
atomic: keep a successful package release, correct only the failed package, and publish
a new version without moving tags or replacing uploaded files.
