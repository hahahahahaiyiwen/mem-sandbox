"""Minimal Milestone 3 policy contracts."""

from dataclasses import dataclass
from typing import cast

from mem_sandbox.core import OperationId, OperationKind, OperationLimits, SessionId
from mem_sandbox.secrets import SecretRef
from mem_sandbox.workspace import SandboxPath


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Normalized operation admission facts."""

    session_id: SessionId
    operation_id: OperationId
    operation_kind: OperationKind
    path: SandboxPath | None
    command_name: str | None
    requested_limits: OperationLimits
    secret_refs: tuple[SecretRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.secret_refs), tuple):
            raise TypeError("secret_refs must be a tuple")
        for secret_ref in self.secret_refs:
            if not isinstance(cast(object, secret_ref), SecretRef):
                raise TypeError("secret_refs must contain SecretRef values")
        if tuple(sorted(self.secret_refs, key=lambda item: item.name)) != self.secret_refs:
            raise ValueError("secret_refs must be sorted by reference name")
        if len(set(self.secret_refs)) != len(self.secret_refs):
            raise ValueError("secret_refs must not contain duplicates")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One explicit allow or deny decision and effective operation limits."""

    allowed: bool
    reason_code: str
    effective_limits: OperationLimits

    def __post_init__(self) -> None:
        allowed = cast(object, self.allowed)
        reason_code = cast(object, self.reason_code)
        if not isinstance(allowed, bool):
            raise TypeError("allowed must be a boolean")
        if not isinstance(reason_code, str):
            raise TypeError("reason_code must be a string")
        if not reason_code.strip():
            raise ValueError("reason_code must not be empty")
