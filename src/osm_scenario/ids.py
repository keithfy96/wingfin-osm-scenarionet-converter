"""Deterministic identifiers for generated artifacts."""

import hashlib


def deterministic_id(namespace: str, *parts: object, length: int = 16) -> str:
    """Return a stable hexadecimal ID for a namespace and ordered values."""
    payload = "\x1f".join((namespace, *(str(part) for part in parts))).encode()
    return hashlib.sha256(payload).hexdigest()[:length]
