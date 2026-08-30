# MemSandbox package

This package contains the framework-neutral MemSandbox implementation.

Core and domain modules must not import agent frameworks. Optional framework integrations
belong under `adapters` and translate framework contracts into the same core API.

Each submodule owns the narrow interfaces it consumes. Do not add a global protocol,
service-locator, or dependency bucket.
