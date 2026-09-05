"""Shared measurement for immutable workspace codec entries."""

from __future__ import annotations

from dataclasses import dataclass

from mem_sandbox.workspace.errors import SnapshotCorruptError
from mem_sandbox.workspace.hashing import hash_directory
from mem_sandbox.workspace.models import (
    ContentHash,
    NodeKind,
    WorkspaceSnapshotEntry,
    WorkspaceTreeStats,
)


@dataclass(slots=True)
class _TreeDirectory:
    children: dict[str, _TreeNode]


type _TreeNode = _TreeDirectory | bytes


def measure_workspace_entries(
    entries: tuple[WorkspaceSnapshotEntry, ...],
) -> WorkspaceTreeStats:
    """Validate and measure one complete explicit workspace tree."""
    root = _TreeDirectory(children={})
    total_bytes = 0

    for entry in sorted(entries, key=lambda item: item.path.value):
        parent = root
        for segment in entry.path.parts[:-1]:
            child = parent.children.get(segment)
            if not isinstance(child, _TreeDirectory):
                raise SnapshotCorruptError(f"snapshot parent directory is missing for {entry.path}")
            parent = child
        if entry.path.name in parent.children:
            raise SnapshotCorruptError(f"snapshot path is duplicated: {entry.path}")
        if entry.kind is NodeKind.DIRECTORY:
            parent.children[entry.path.name] = _TreeDirectory(children={})
        else:
            assert entry.content is not None
            parent.children[entry.path.name] = entry.content
            total_bytes += len(entry.content)

    return WorkspaceTreeStats(
        total_bytes=total_bytes,
        node_count=len(entries) + 1,
        root_hash=_tree_node_hash(root),
    )


def _tree_node_hash(node: _TreeNode) -> ContentHash:
    if isinstance(node, bytes):
        return ContentHash.from_bytes(node)

    hashes: dict[int, ContentHash] = {}
    pending: list[tuple[_TreeNode, bool]] = [(node, False)]
    while pending:
        current, visited = pending.pop()
        if isinstance(current, bytes):
            hashes[id(current)] = ContentHash.from_bytes(current)
            continue
        if not visited:
            pending.append((current, True))
            pending.extend((child, False) for child in current.children.values())
            continue
        hashes[id(current)] = hash_directory(
            (
                name,
                NodeKind.FILE if isinstance(child, bytes) else NodeKind.DIRECTORY,
                hashes[id(child)],
            )
            for name, child in current.children.items()
        )
    return hashes[id(node)]
