"""Session-owned resource-scope implementations."""


class NoOpSessionResourceScope:
    """A concrete scope with no closeable resources."""

    __slots__ = ()

    async def close(self) -> None:
        return None
