"""Embedding provider abstraction (spec 2.10.3, 2.10.4).

The retrieval layer indexes agreement text as vectors. Which model produces
those vectors is configuration, not code:

  - ``openai``  → OpenAI embeddings API (``text-embedding-3-small``, 1536
    dimensions by default). Requires ``OPENAI_API_KEY``; calls fail closed
    with :class:`EmbeddingProviderError` rather than silently returning
    garbage vectors.
  - ``hash``    → deterministic hashing bag-of-words embedding (the previous
    behavior). No network, no credentials, stable across runs — used by the
    SQLite test suite and as an explicit dev fallback.

Configuration::

    EMBEDDING_PROVIDER=openai        # 'openai' | 'hash'
    EMBEDDING_MODEL=text-embedding-3-small
    EMBEDDING_DIM=1536               # must match the pgvector column

Design rules (spec 2.10 / 1.19):

  - The provider must never fabricate vectors when unconfigured: the
    ``openai`` provider raises instead of degrading, so mixed-model indexes
    cannot happen by accident.
  - Every vector records which model produced it (``KnowledgeChunk.embedding_model``)
    so re-indexing after a model change is detectable.
  - Batching: one API call per up-to-``batch_size`` inputs keeps indexing a
    full agreement version to a handful of requests.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from typing import Iterable, Sequence

from app.core.config import get_settings_lazy

logger = logging.getLogger(__name__)

# Dimension of the pgvector column / HNSW index (spec 2.10.6). OpenAI's
# text-embedding-3-small outputs 1536-dim vectors.
EMBEDDING_DIM = 1536

_HASH_EMBEDDING_DIM = 256
_HASH_MODEL_NAME = "hash-bow-256"
_HASH_PROJECTED_MODEL_NAME = "hash-bow-256@1536"
_OPENAI_MODEL_DEFAULT = "text-embedding-3-small"


class EmbeddingProviderError(Exception):
    """Raised when the configured embedding provider cannot produce vectors."""


# ---------------------------------------------------------------------------
# Hashing fallback (pure Python, deterministic, no dependencies)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{2,}", (text or "").lower())


def _hash_embed(tokens: Iterable[str], dim: int = _HASH_EMBEDDING_DIM) -> list[float]:
    """Deterministic hashing bag-of-words embedding (stable, sparse)."""
    vec = [0.0] * dim
    for tok in tokens:
        h1 = int(hashlib.md5(f"a:{tok}".encode()).hexdigest(), 16) % dim
        h2 = int(hashlib.md5(f"b:{tok}".encode()).hexdigest(), 16) % dim
        vec[h1] += 1.0
        vec[h2] += 0.5
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class EmbeddingProvider(ABC):
    """Interface for embedding models."""

    name: str = "abstract"

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input."""


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic local embedding — dev/test only, no semantic quality.

    Produces the native 256-dim hashing vector projected into the configured
    ``dim`` (zero-padded to 1536 for the pgvector column). Projection makes
    the fallback usable against the production column without changing DDL;
    set ``EMBEDDING_PROVIDER=hash`` only in dev/test.
    """

    name = _HASH_MODEL_NAME

    def __init__(self, dim: int | None = None):
        self.dim = dim or _HASH_EMBEDDING_DIM
        if self.dim < _HASH_EMBEDDING_DIM:
            raise EmbeddingProviderError(
                f"EMBEDDING_DIM={self.dim} is smaller than the hashing "
                f"embedding width {_HASH_EMBEDDING_DIM}"
            )
        if self.dim != _HASH_EMBEDDING_DIM:
            self.name = f"{_HASH_MODEL_NAME}@{self.dim}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        native = [_hash_embed(_tokenize(t)) for t in texts]
        if self.dim == _HASH_EMBEDDING_DIM:
            return native
        pad = self.dim - _HASH_EMBEDDING_DIM
        return [v + [0.0] * pad for v in native]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embeddings API via ``httpx`` (already a project dependency,
    mirroring ``ai_service.py`` — no SDK required).

    Calls fail closed with :class:`EmbeddingProviderError` on any HTTP or
    schema error so callers never index vectors from a half-understood
    response.
    """

    name: str

    def __init__(self, *, api_key: str, model: str | None = None, dim: int | None = None):
        self._api_key = api_key
        self.model = model or _OPENAI_MODEL_DEFAULT
        self.name = self.model
        # Explicit dim override lets deployments use e.g. text-embedding-3-large
        # or Matryoshka-truncated vectors without changing the column.
        self.dim = dim or EMBEDDING_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        import httpx

        vectors: list[list[float]] = []
        # The API accepts up to 2048 inputs per request; 128 keeps request
        # sizes moderate for long contract chunks.
        batch_size = 128
        with httpx.Client(timeout=60.0) as client:
            for start in range(0, len(texts), batch_size):
                batch = [t[:32000] for t in texts[start : start + batch_size]]
                body: dict = {"model": self.model, "input": batch}
                if self.model.startswith("text-embedding-3"):
                    body["dimensions"] = self.dim
                try:
                    resp = client.post(
                        "https://api.openai.com/v1/embeddings",
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except Exception as exc:
                    raise EmbeddingProviderError(
                        f"OpenAI embedding call failed: {type(exc).__name__}: {exc}"
                    ) from exc
                try:
                    data = sorted(payload["data"], key=lambda d: d["index"])
                    vectors.extend([list(map(float, d["embedding"])) for d in data])
                except (KeyError, TypeError, ValueError) as exc:
                    raise EmbeddingProviderError(
                        f"OpenAI embedding response malformed: {exc}"
                    ) from exc
        return vectors


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """Resolve the configured embedding provider (cached)."""
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings_lazy()
    provider_name = (getattr(settings, "embedding_provider", "") or "hash").strip().lower()
    if provider_name == "openai":
        api_key = getattr(settings, "openai_api_key", None)
        secret = api_key.get_secret_value() if api_key else None
        if not secret:
            raise EmbeddingProviderError(
                "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set; "
                "set a key or use EMBEDDING_PROVIDER=hash"
            )
        model = getattr(settings, "embedding_model", None) or None
        dim = getattr(settings, "embedding_dim", None)
        _provider = OpenAIEmbeddingProvider(
            api_key=secret, model=model, dim=int(dim) if dim else None
        )
    elif provider_name == "hash":
        dim = getattr(settings, "embedding_dim", None)
        _provider = HashEmbeddingProvider(dim=int(dim) if dim else None)
    else:
        raise EmbeddingProviderError(
            f"Unknown EMBEDDING_PROVIDER {provider_name!r}; use 'openai' or 'hash'"
        )
    return _provider


def reset_embedding_provider() -> None:
    """Clear the cached provider (used by tests)."""
    global _provider
    _provider = None
