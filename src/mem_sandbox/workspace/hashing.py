"""Deterministic workspace hashing primitives."""

from collections.abc import Iterable
from hashlib import sha256

from mem_sandbox.workspace.models import ContentHash, NodeKind


def hash_directory(
    children: Iterable[tuple[str, NodeKind, ContentHash]],
) -> ContentHash:
    """Hash ordered child names, kinds, and hashes without host metadata."""
    digest = sha256()
    digest.update(b"mem-sandbox-directory-v1\x00")
    for name, kind, content_hash in sorted(children):
        name_bytes = name.encode("utf-8")
        digest.update(b"F" if kind is NodeKind.FILE else b"D")
        digest.update(len(name_bytes).to_bytes(4, "big"))
        digest.update(name_bytes)
        digest.update(bytes.fromhex(content_hash.value))
    return ContentHash(digest.hexdigest())
