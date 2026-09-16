"""Clause integrity hashing (spec 1.8.7)."""

import hashlib


def calculate_clause_hash(content: str) -> str:
    """SHA-256 of clause text - integrity verification for every version."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
