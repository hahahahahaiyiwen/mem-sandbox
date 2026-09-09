# Official OpenAI Agent Sample

## Purpose and boundary

This package configures an official OpenAI model for the
[OpenAI Agents SDK sample runner](../../README.md). It owns only environment
configuration, official `AsyncOpenAI` client construction, the Responses model adapter,
and the executable provider entry point.

Scenarios, MemSandbox composition, agent capability binding, verification, inspection,
and cleanup behavior are shared with the Azure OpenAI sample.

## Requirements

- An official OpenAI API key.
- A model available to that account that supports Responses API function/tool calling.
- The repository dependencies installed with `uv sync --all-groups`.

The sample pins the official `https://api.openai.com/v1` endpoint. It does not honor an
ambient `OPENAI_BASE_URL`; custom gateways and other OpenAI-compatible base URLs are not
supported by this provider package.

## Configuration

Set both variables:

| Variable | Meaning |
|---|---|
| `OPENAI_API_KEY` | API key used only to construct the official OpenAI client |
| `OPENAI_MODEL` | Model identifier passed to the OpenAI Agents SDK |

PowerShell:

```powershell
$env:OPENAI_API_KEY = '<api-key>'
$env:OPENAI_MODEL = '<model>'
uv run python -m samples.openai_agents_sdk.providers.openai `
  --scenario workspace-edit `
  --inspect
```

Bash:

```sh
export OPENAI_API_KEY="<api-key>"
export OPENAI_MODEL="<model>"
uv run python -m samples.openai_agents_sdk.providers.openai \
  --scenario workspace-edit \
  --inspect
```

Do not put credentials in source files, command history, issue comments, test fixtures,
or committed `.env` files. `OpenAISettings` hides the key from its representation, and
the sample never writes it into MemSandbox.

## Shared scenarios and inspection

List all scenarios without credentials or network access:

```console
uv run python -m samples.openai_agents_sdk.providers.openai --list-scenarios
```

Use `--inspect` after successful verification or `--inspect-on-failure` before failed
state is cleaned up. The inspection CLI executes only the constrained MemSandbox
command language.

See the [SDK scenario design](../../scenarios/README.md) for the scenario
catalog and lifecycle invariants.

## Runtime and testing

A live run sends billable requests to the official OpenAI API and requires network
access. OpenAI Agents SDK tracing is disabled by the shared runner.

Required tests construct and close the client without issuing a request. Scenario tests
use a deterministic `Model` double, so CI requires no API key or inference endpoint.

## Maintenance

Keep only official OpenAI configuration and model construction in this package.
SDK-specific flags, scenarios, verification, and cleanup belong in
`samples.openai_agents_sdk`; reusable inspection and service composition belong in
`samples.shared`.
