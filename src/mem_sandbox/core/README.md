# Core foundation

The `core` package owns framework-neutral identity, timing, error, and operation metadata
conventions shared by MemSandbox domain boundaries.

## Invariants

- Session, operation, and snapshot IDs are immutable, typed, non-nil UUID values.
- Revisions are immutable non-negative integers and advance explicitly.
- Time enters behavior through a narrow `Clock` protocol and is represented as aware UTC.
- UUID creation enters behavior through a narrow `UuidGenerator` protocol.
- Expected failures use stable domain categories and codes.
- Request and result metadata is immutable and uses domain identifiers rather than raw
  strings or dictionaries.

## Dependency boundary

This package uses only the Python standard library. It must not import an agent SDK,
adapter, host filesystem implementation, or service locator.

## Maintenance

Add specific domain request and result types beside the component that owns their
behavior. Change shared categories only when adapters and existing callers can preserve
their meaning, and update foundation tests with every contract change.

The [Milestone 8 authority/accounting design](../../../docs/product-validation/authority-accounting-design.md)
keeps future resource identities, usage facts, and settlement metadata typed and
framework-neutral while deferring any shared categories until a concrete implementation
needs them.
