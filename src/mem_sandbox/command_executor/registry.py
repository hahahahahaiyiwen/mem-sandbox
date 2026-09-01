"""Immutable case-sensitive virtual command registration."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from mem_sandbox.command_executor.models import (
    CommandContext,
    CommandDescriptor,
    CommandRequest,
    CommandResult,
)


class VirtualCommand(Protocol):
    """One focused pipeline-ready virtual command handler."""

    @property
    def descriptor(self) -> CommandDescriptor: ...

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult: ...


class CommandRegistry:
    """An immutable duplicate-safe command and alias lookup."""

    def __init__(self, commands: tuple[VirtualCommand, ...]) -> None:
        _require_command_tuple(commands)
        lookup: dict[str, VirtualCommand] = {}
        descriptors: list[CommandDescriptor] = []
        for command in commands:
            descriptor = command.descriptor
            descriptors.append(descriptor)
            for name in (descriptor.name, *descriptor.aliases):
                if name in lookup:
                    raise ValueError(f"duplicate command name or alias: {name}")
                lookup[name] = command
        self._lookup: Mapping[str, VirtualCommand] = MappingProxyType(lookup)
        self._descriptions = tuple(descriptors)

    def resolve(self, name: str) -> VirtualCommand | None:
        """Resolve one exact command name or explicit alias."""
        return self._lookup.get(name)

    @property
    def descriptions(self) -> tuple[CommandDescriptor, ...]:
        """Return the only authoritative command-description collection."""
        return self._descriptions


def _require_command_tuple(value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError("commands must be a tuple")
