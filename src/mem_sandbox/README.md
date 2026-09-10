# MemSandbox package

This package contains the framework-neutral MemSandbox implementation.

The `mem-sandbox` distribution owns only the `mem_sandbox` namespace and declares no
runtime dependencies. Core and domain modules must not import agent frameworks.
Framework integrations belong in separate distributions and translate framework
contracts into the same core API; the OpenAI Agents SDK adapter is
`mem-sandbox-openai-agents`.

Each submodule owns the narrow interfaces it consumes. Do not add a global protocol,
service-locator, or dependency bucket.
