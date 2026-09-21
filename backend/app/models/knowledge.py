"""Knowledge retrieval models (spec 2.10).

KnowledgeChunk stores legal-aware chunks of agreement text with the full
retrieval metadata (organization, agreement, version, clause, type, parties,
effective dates, jurisdiction, classification) so permission-filtered and
temporal retrieval are possible.

Embeddings (spec 2.10.6): on PostgreSQL the ``embedding`` column is a native
pgvector ``vector(1536)`` with an HNSW cosine index; on other dialects
(the SQLite test suite) it degrades to a JSON-encoded list and similarity is
computed in Python. ``retrieval_service`` picks the matching search strategy
by dialect, so callers see one interface.

The ``EmbeddingVectorType`` below is intentionally self-contained (no
``pgvector`` Python package): values cross the wire in pgvector's text
format ('[0.1,0.2,...]') which the server casts to/from ``vector`` — no
per-connection codec registration is needed with asyncpg.
"""

import json
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Dimension of the pgvector column / HNSW index (spec 2.10.6). Must match
# EMBEDDING_PROVIDER output: OpenAI text-embedding-3-small -> 1536.
EMBEDDING_DIM = 1536


class EmbeddingVectorType(UserDefinedType):
    """pgvector ``vector(dim)`` on PostgreSQL; JSON list elsewhere.

    PostgreSQL: DDL renders ``VECTOR(dim)``; values bind as the pgvector
    text representation and are parsed back from it on read; the comparator
    exposes pgvector distance operators (``<=>`` for cosine).
    Other dialects (SQLite tests): values are stored as JSON strings.

    Registered in ``ischema_names`` so schema reflection (and therefore
    ``alembic check``) sees the same type the model declares.
    """

    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = int(dim)

    # --- DDL ---------------------------------------------------------------

    def get_col_spec(self, **kw) -> str:
        return f"VECTOR({self.dim})"

    # --- Bind / result -----------------------------------------------------

    def bind_processor(self, dialect):
        if dialect.name == "postgresql":

            def process_pg(value):
                if value is None:
                    return None
                if isinstance(value, str):
                    return value  # already in pgvector text form
                return "[" + ",".join(repr(float(v)) for v in value) + "]"

            return process_pg

        def process_json(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return json.dumps([float(v) for v in value])

        return process_json

    def result_processor(self, dialect, coltype):
        if dialect.name == "postgresql":

            def process_pg(value):
                if value is None:
                    return None
                if isinstance(value, (list, tuple)):
                    return [float(v) for v in value]
                # text form '[0.1,0.2,...]'
                return [
                    float(v)
                    for v in str(value).strip("[]").split(",")
                    if v.strip()
                ]

            return process_pg

        def process_json(value):
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                return [float(v) for v in value]
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return None

        return process_json

    # --- Operators (pgvector) ------------------------------------------------

    class Comparator(UserDefinedType.Comparator):
        def cosine_distance(self, other):
            # pgvector: embedding <=> query
            return self.op("<=>", return_type=postgresql.DOUBLE_PRECISION)(other)

        def l2_distance(self, other):
            return self.op("<->", return_type=postgresql.DOUBLE_PRECISION)(other)

        def max_inner_product(self, other):
            return self.op("<#>", return_type=postgresql.DOUBLE_PRECISION)(other)

    comparator_factory = Comparator

    # --- Schema-drift comparison (alembic check) -----------------------------

    def compare_against_backend(self, dialect, conn_type):
        """Treat any pg vector(dim) reflected type as equal when dims match.

        Reflection may hand back this class (via ischema_names below) or a
        generic NULLType for USER-DEFINED columns depending on the
        SQLAlchemy path; accept both so `alembic check` does not generate
        phantom alter-column operations. Dimension mismatches still count
        as drift when the reflected type is this class.
        """
        if isinstance(conn_type, EmbeddingVectorType):
            return conn_type.dim == self.dim
        if dialect.name == "postgresql" and type(conn_type).__name__ in (
            "NULLType",
            "NullType",
        ):
            return True
        return None  # defer to default comparison


# Reflect pgvector columns back into this type (alembic check / inspector).
postgresql.base.ischema_names["vector"] = EmbeddingVectorType


class KnowledgeChunk(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A chunk of agreement text indexed for semantic retrieval."""

    __tablename__ = "knowledge_chunks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_clauses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    agreement_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Deterministic content hash for change detection / re-indexing.
    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    # Embedding vector (list[float]) — native pgvector on PostgreSQL,
    # JSON on other dialects. NULL means "not embedded yet"; retrieval
    # still finds such chunks via the keyword score.
    embedding: Mapped[list | None] = mapped_column(
        EmbeddingVectorType(EMBEDDING_DIM),
        nullable=True,
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # --- Retrieval metadata -------------------------------------------------
    party_ids: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    jurisdiction: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )

    classification: Mapped[str | None] = mapped_column(
        String(50),
        nullable=False,
        default="internal",
        # 'public', 'internal', 'confidential', 'restricted'
    )

    is_current: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        index=True,
    )

    # When this chunk stopped being current (superseded by a new version /
    # amendment). Null means it is still the current term.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "agreement_id",
            "version_id",
            "chunk_index",
            name="uq_knowledge_chunk_agreement_version_index",
        ),
        # HNSW approximate-nearest-neighbour index for cosine search
        # (spec 2.10.13 hybrid retrieval). PG-only options are ignored by
        # other dialects at DDL time.
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    agreement = relationship("Agreement")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<KnowledgeChunk {self.agreement_id} v{self.version_id} "
            f"[{self.chunk_index}] current={self.is_current}>"
        )
