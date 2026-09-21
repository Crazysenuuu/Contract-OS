"""Live pgvector E2E check: index + retrieve against real PostgreSQL.

Run with a pgvector-enabled DATABASE_URL. Verifies:
- vector(1536) column round-trip via the dialect-aware type
- SQL cosine-distance ordering (the pgvector retrieval path)
- permission filtering inside the SQL path
- keyword-only scoring for chunks without embeddings
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from app.models.knowledge import KnowledgeChunk  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.agreement import Agreement, AgreementVersion  # noqa: E402
from app.models.agreement_type import AgreementType  # noqa: E402
from app.services.retrieval_service import index_agreement_version, retrieve  # noqa: E402


async def main() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    failures: list[str] = []

    async with session_factory() as db:
        # Column type sanity ('vector' arrives as USER-DEFINED + udt_name)
        col_type = (
            await db.execute(text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name='knowledge_chunks' AND column_name='embedding'"
            ))
        ).scalar()
        print(f"embedding column type: {col_type}")
        if col_type != "vector":
            failures.append(f"expected vector column, got {col_type!r}")

        # Fixtures
        org = Organization(
            name=f"vec-org-{uuid.uuid4().hex[:8]}",
            slug=f"vec-org-{uuid.uuid4().hex[:8]}",
            country="US",
            timezone="UTC",
        )
        db.add(org)
        await db.flush()
        atype = AgreementType(
            key=f"nda_{uuid.uuid4().hex[:8]}",
            name=f"NDA-{uuid.uuid4().hex[:8]}",
            description="NDA",
            category="confidentiality",
            schema={"questions": [], "clauses": []},
        )
        db.add(atype)
        await db.flush()
        user = User(
            email=f"vec-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            name="Vector Check",
        )
        db.add(user)
        await db.flush()
        agreement = Agreement(
            organization_id=org.id,
            agreement_type_id=atype.id,
            title="Vector Check NDA",
            status="draft",
            created_by=user.id,
            data={},
        )
        db.add(agreement)
        await db.flush()

        import hashlib as _hashlib

        version_content = "Vector check body"
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            content=version_content,
            content_hash=_hashlib.sha256(version_content.encode()).hexdigest(),
            status="draft",
            created_by=user.id,
        )
        db.add(version)
        await db.flush()

        content = (
            "1. Confidentiality. Each party shall protect Confidential Information. "
            "2. Liability. Aggregate liability shall not exceed one million dollars. "
            "3. Term. This agreement runs for twelve months and renews automatically. "
        )
        chunks = await index_agreement_version(
            db,
            organization_id=org.id,
            agreement_id=agreement.id,
            version_id=version.id,
            content=content,
            agreement_type="nda",
        )
        print(f"indexed chunks: {len(chunks)}, model={chunks[0].embedding_model}, dims={len(chunks[0].embedding)}")

        # Round-trip: read back the vector from PG
        stored = (
            await db.execute(
                text("SELECT embedding FROM knowledge_chunks WHERE id = :id"),
                {"id": str(chunks[0].id)},
            )
        ).scalar()
        if stored is None:
            failures.append("embedding came back NULL from PG")
        else:
            dims = len(str(stored).strip("[]").split(","))
            print(f"stored vector dims from PG: {dims}")
            if dims != len(chunks[0].embedding):
                failures.append(f"dim mismatch: {dims} != {len(chunks[0].embedding)}")

        # HNSW index exists
        idx = (
            await db.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname='ix_knowledge_chunks_embedding_hnsw'"
            ))
        ).scalar()
        if not idx:
            failures.append("HNSW index missing")
        else:
            print("HNSW index: present")

        # pgvector-path retrieval: relevant question ranks the liability chunk first
        hits = await retrieve(
            db,
            organization_id=org.id,
            question="What is the limit of liability?",
            accessible_agreement_ids=[agreement.id],
            min_score=0.0,
        )
        if not hits:
            failures.append("no hits from pgvector retrieval")
        else:
            print(f"top hit score={hits[0].score:.4f} content={hits[0].content[:60]!r}")
            if "Liability" not in hits[0].content:
                failures.append("liability question did not rank the liability chunk first")

        # Permission filter inside the SQL path
        hits = await retrieve(
            db,
            organization_id=org.id,
            question="liability",
            accessible_agreement_ids=[uuid.uuid4()],  # some other agreement
            min_score=0.0,
        )
        if hits:
            failures.append("permission filter leaked chunks from inaccessible agreements")
        else:
            print("permission filter: enforced")

        # Keyword-only path: chunk with NULL embedding still retrievable
        await db.execute(
            text("UPDATE knowledge_chunks SET embedding = NULL WHERE agreement_id = :a"),
            {"a": str(agreement.id)},
        )
        await db.commit()
        hits = await retrieve(
            db,
            organization_id=org.id,
            question="renews automatically",
            accessible_agreement_ids=[agreement.id],
            min_score=0.0,
        )
        if not hits:
            failures.append("keyword-only retrieval failed for NULL-embedding chunks")
        else:
            print(f"keyword-only retrieval: {len(hits)} hit(s), top score={hits[0].score:.4f}")

        await db.rollback()

    await engine.dispose()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll pgvector E2E checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
