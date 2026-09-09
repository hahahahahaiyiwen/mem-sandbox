# Azure OpenAI Agent Sample

## Purpose and boundary

This sample suite demonstrates OpenAI Agents SDK `SandboxAgent` instances using an Azure
OpenAI model deployment to complete stateful tasks through
`InMemorySandboxCapability`.

The application owns:

- Azure endpoint, deployment, API version, and credentials;
- the concrete Azure model client;
- MemSandbox service composition and lifetime;
- creation and deletion of the SDK sandbox session;
- the prompt and host-side result verification.

The model receives only the four capability tools: `execute`, `read_file`, `write_file`,
and `apply_patch`. It cannot choose a sandbox handle, manage lifecycle or snapshots,
access the host filesystem, select a host shell, start a process, mount storage, or open
a network connection through MemSandbox.

## Supported endpoint

The runnable path supports either public Azure OpenAI resource endpoint shape:

```text
https://<resource-name>.openai.azure.com
https://<resource-name>.cognitiveservices.azure.com
```

The deployment must support Chat Completions function/tool calling. The deployment name,
not the underlying model family name, is passed to the OpenAI Agents SDK.

Azure AI Foundry project endpoints, serverless model endpoints, `models.ai.azure.com`,
sovereign-cloud suffixes, custom gateways, and other OpenAI-compatible endpoints are not
claimed as supported by this sample. They require their own endpoint/client validation.

## Installation

From the repository root:

```console
uv sync --all-groups
```

The runtime integration remains optional for package users:

```console
pip install -e ".[openai-agents]"
```

## Configuration

Set all four variables:

| Variable | Meaning |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Public Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_KEY` | API key used only to construct the Azure client |
| `AZURE_OPENAI_API_VERSION` | Explicit API version supported by the deployment |
| `AZURE_OPENAI_DEPLOYMENT` | Azure deployment name used as the model identifier |

PowerShell example:

```powershell
$env:AZURE_OPENAI_ENDPOINT = 'https://<resource-name>.openai.azure.com'
$env:AZURE_OPENAI_API_KEY = '<key>'
$env:AZURE_OPENAI_API_VERSION = '<api-version>'
$env:AZURE_OPENAI_DEPLOYMENT = '<deployment-name>'
uv run python -m samples.azure_openai_agent --scenario workspace-edit --inspect
```

Do not put credentials in source files, command history, issue comments, test fixtures,
or committed `.env` files. The configuration object hides the API key from its
representation, and the sample never prints it.

API-key authentication is the smallest local demonstration path. Azure-hosted production
applications should prefer a specific managed identity with the narrowest applicable
Azure RBAC role. Local passwordless development may use Azure Identity's
`DefaultAzureCredential`; production should use `ManagedIdentityCredential` or another
explicit workload credential. Adding that optional dependency and token-provider path is
deferred until the sample's first live run proves it is needed.

## Scenarios

Use `--list-scenarios` to print the registry. Every scenario provides deterministic
initial files, bounded agent stages, and host-side verification.

| Scenario | Demonstrated behavior |
|---|---|
| `workspace-edit` | Create, read, hash-guarded patch, and exact verification |
| `incident-triage` | Search seeded logs and produce a verified incident report |
| `config-migration` | Atomically migrate multiple configuration files |
| `data-pipeline` | Build a deterministic artifact with pipes and redirection |
| `policy-recovery` | Recover from a real policy denial without escalating authority |
| `quota-recovery` | Recover from a real file-quota failure with a compact result |
| `multi-agent-handoff` | Planner, implementer, and reviewer share one session |
| `snapshot-branching` | Run isolated alternatives from one snapshot and inspect the selected fork |

The host-side verifier is authoritative. A natural-language final response without the
required in-memory state is a failed scenario.

## Interactive inspection

Pass `--inspect` to open an interactive CLI after successful verification. Pass
`--inspect-on-failure` to inspect the live session before a failed run is cleaned up.
Both options connect to the same MemSandbox session used by the final agent stage:

```text
MemSandbox inspection CLI
Commands run in the constrained in-memory environment.
Type 'help' for commands or 'exit' to close the sandbox.
mem-sandbox:/workspace> cat /workspace/demo/report.txt
status=complete
mem-sandbox:/workspace> exit
```

Without either option, the program verifies and cleans up without reading stdin. The
prompt follows the session's current directory, so `cd` persists between commands.
The CLI accepts the documented constrained command language, including pipes,
conditionals, and supported redirection. It never invokes a host shell. `help` prints the
supported command summary; `exit`, `quit`, EOF (Ctrl+D, or Ctrl+Z then Enter on Windows),
or process interruption ends inspection. Any CLI mutations are temporary because leaving
the CLI closes and deletes the in-memory sandbox.

## Lifecycle

```text
construct Azure client and model
  -> construct MemSandbox service
  -> create SDK sandbox session
  -> run SandboxAgent with tracing disabled
  -> verify final file through the host session
  -> optionally inspect the same session on success or failure
  -> close SDK session
  -> delete backend handle
  -> close MemSandbox service
  -> close Azure client
```

The agent run does not own the service or Azure client. Cleanup occurs for normal
completion, model/tool failure, and cancellation.

OpenAI Agents SDK tracing is disabled by default. Azure model credentials do not
configure OpenAI platform tracing, and the sample does not send prompts or tool content
to a separate trace exporter.

## Expected output

The exact model wording varies. A successful run prints the final response and verified artifacts. With `--inspect`, it
then opens the CLI:

```text
Verified /workspace/demo/report.txt:
status=complete
mem-sandbox:/workspace>
```

The run performs a billable Azure model request and requires network access. Required CI
uses a deterministic model double and performs no Azure/OpenAI request.

## Limitations and troubleshooting

- **Invalid endpoint:** use an HTTPS Azure OpenAI resource endpoint ending in
  `.openai.azure.com` or `.cognitiveservices.azure.com`, not a Foundry project or
  serverless-model URL.
- **Authentication failure:** verify the API key belongs to the configured resource.
- **Deployment not found:** set the Azure deployment name rather than a model family
  such as `gpt-4o`.
- **Tool calls unsupported:** choose a deployment/API version combination that supports
  Chat Completions tool calling.
- **Task did not verify:** inspect the model response for tool-input repair attempts. The
  sample intentionally fails if verified workspace content is not exact. Re-run with
  `--inspect-on-failure` to inspect the pre-cleanup state.
- **Command limitations:** MemSandbox provides a constrained virtual command language,
  not Python execution, arbitrary executables, `sh -lc`, or a host shell.
- **CLI changes disappeared:** the sample intentionally deletes its in-memory backend
  when the CLI exits.

## Maintenance

Keep Azure/provider configuration in this sample, OpenAI translation in
`mem_sandbox.integrations.openai_agents`, and sandbox behavior in core modules. Update
the deterministic model tests whenever scenario invariants or capability contracts
change. See [scenario design](./scenarios/README.md).
