# Samples

Samples demonstrate supported product integrations without changing runtime ownership
boundaries. Each sample owns its configuration and composition code locally so an
example does not become a production abstraction before repeated application usage
proves one is needed.

Required CI coverage must be deterministic and network-free. Live provider runs are
opt-in, credentialed by the application environment, and documented with their cost,
network, and compatibility assumptions.

Available samples:

- [SDK-independent shared utilities](./shared/README.md)
- [OpenAI Agents SDK runner and scenarios](./openai_agents_sdk/README.md)
- [Azure OpenAI provider](./openai_agents_sdk/providers/azure_openai/README.md)
- [Official OpenAI provider](./openai_agents_sdk/providers/openai/README.md)
