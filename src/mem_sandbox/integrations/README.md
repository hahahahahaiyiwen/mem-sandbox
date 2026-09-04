# Integration Package

## Purpose

This package owns optional framework-specific adapters. The dependency-free core does
not import this package, and integrations may depend only on public core package exports.

## Boundary rules

- Each framework integration owns its native client, session, capability, and translation
  types.
- Framework dependencies are declared through optional extras.
- Domain behavior remains in the owning core modules.
- Integrations must not fall back to the host filesystem, host shell, or global session
  state.
- Shared production abstractions are extracted only after two adapters prove identical
  behavior.

Each integration subpackage maintains its own supported dependency range, compatibility
policy, implementation status, and framework-specific invariants.
