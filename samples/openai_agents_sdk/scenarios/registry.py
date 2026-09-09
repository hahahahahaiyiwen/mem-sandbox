"""Stable registry of runnable provider-neutral agent scenarios."""

from samples.openai_agents_sdk.scenarios.config_migration import (
    SCENARIO as CONFIG_MIGRATION,
)
from samples.openai_agents_sdk.scenarios.data_pipeline import SCENARIO as DATA_PIPELINE
from samples.openai_agents_sdk.scenarios.incident_triage import (
    SCENARIO as INCIDENT_TRIAGE,
)
from samples.openai_agents_sdk.scenarios.multi_agent_handoff import (
    SCENARIO as MULTI_AGENT_HANDOFF,
)
from samples.openai_agents_sdk.scenarios.policy_recovery import (
    SCENARIO as POLICY_RECOVERY,
)
from samples.openai_agents_sdk.scenarios.quota_recovery import (
    SCENARIO as QUOTA_RECOVERY,
)
from samples.openai_agents_sdk.scenarios.snapshot_branching import (
    SCENARIO as SNAPSHOT_BRANCHING,
)
from samples.openai_agents_sdk.scenarios.types import ScenarioDefinition
from samples.openai_agents_sdk.scenarios.workspace_edit import (
    SCENARIO as WORKSPACE_EDIT,
)

_SCENARIOS = (
    WORKSPACE_EDIT,
    INCIDENT_TRIAGE,
    CONFIG_MIGRATION,
    DATA_PIPELINE,
    POLICY_RECOVERY,
    QUOTA_RECOVERY,
    MULTI_AGENT_HANDOFF,
    SNAPSHOT_BRANCHING,
)
_BY_NAME = {scenario.name: scenario for scenario in _SCENARIOS}


def list_scenarios() -> tuple[ScenarioDefinition, ...]:
    """Return scenarios in stable display order."""
    return _SCENARIOS


def get_scenario(name: str) -> ScenarioDefinition:
    """Resolve one scenario name or raise a configuration error."""
    try:
        return _BY_NAME[name]
    except KeyError as error:
        choices = ", ".join(_BY_NAME)
        raise ValueError(f"unknown scenario {name!r}; choose one of: {choices}") from error
