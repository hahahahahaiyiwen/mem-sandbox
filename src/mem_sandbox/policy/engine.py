"""Concrete minimal policy behavior."""

from mem_sandbox.policy.models import PolicyDecision, PolicyRequest


class AllowAllPolicyEngine:
    """Explicitly allow every Milestone 3 operation without changing limits."""

    __slots__ = ()

    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=True,
            reason_code="allow_all",
            effective_limits=request.requested_limits,
        )
