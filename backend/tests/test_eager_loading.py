"""Model-layer hardening: hot relationship collections must be eager.

Serializers and services on this app's hot paths read relationship
collections AFTER awaited queries on AsyncSession sessions. A default
lazy="select" relationship raises MissingGreenlet there (lazy load emits
IO outside the greenlet that owns the connection) — the exact failure
class found during the 2026-09 async-serializer audit
(agreement_changes.accept_change / forecasting serializers).

Two layers of protection:

1. Static pins: every hardened relationship must keep lazy="selectin"
   (fails loudly if the setting is dropped).
2. Behavioral proof: fetch the row in a FRESH session, then touch the
   collections SYNCHRONOUSLY — the serializer scenario. With
   lazy="select" this raises MissingGreenlet; with selectin the
   collections were already loaded by the fetch itself.
"""

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParty
from app.models.agreement_type import AgreementType
from app.models.approval import ApprovalDefinition, ApprovalRecord, ApprovalStage
from app.models.billing import Invoice
from app.models.clause import Clause, ClauseVersion
from app.models.external_party import ExternalParty
from app.models.forecasting import ForecastPrediction, ForecastRun
from app.models.ingestion import IngestionJob
from app.models.legal_entity import LegalEntity
from app.models.negotiation import AgreementChange
from app.models.template import Template
from app.models.termination import AgreementTermination, TerminationSettlement
from app.models.user import User

# (model class, relationship name) for every collection a serializer or
# service reads after awaits. Keep in sync with the lazy="selectin"
# comments in the models.
HOT_EAGER_RELATIONSHIPS = [
    (Agreement, "versions"),
    (Agreement, "parties"),
    (Agreement, "participants"),
    (AgreementChange, "items"),
    (ForecastRun, "predictions"),
    (Invoice, "lines"),
    (IngestionJob, "documents"),
    (Template, "versions"),
    (Template, "variables"),
    (Clause, "versions"),
    (ClauseVersion, "variables"),
    (ClauseVersion, "conditions"),
    (ClauseVersion, "jurisdiction_bindings"),
    (User, "memberships"),
    (User, "sessions"),
    # --- sweep of less-hot modules (same MissingGreenlet exposure) ---
    (AgreementTermination, "post_termination_obligations"),
    (AgreementTermination, "settlement"),
    (TerminationSettlement, "items"),
    (ExternalParty, "signature_events"),
    (ExternalParty, "sessions"),
    (ApprovalDefinition, "stages"),
    (ApprovalStage, "steps"),
    (ApprovalRecord, "decisions"),
]


class TestHotRelationshipsAreSelectin:
    @pytest.mark.parametrize(
        "model,rel_name",
        HOT_EAGER_RELATIONSHIPS,
        ids=[f"{m.__name__}.{r}" for m, r in HOT_EAGER_RELATIONSHIPS],
    )
    def test_relationship_is_eager(self, model, rel_name):
        rel = inspect(model).relationships[rel_name]
        assert rel.lazy == "selectin", (
            f"{model.__name__}.{rel_name} must stay lazy='selectin': "
            "hot serializer/service paths read it after awaits, and a "
            "default lazy load there raises MissingGreenlet"
        )


# ---------------------------------------------------------------------------
# Behavioral proof: fresh session + synchronous access (the serializer
# scenario). Only models with fully-known required fields are exercised
# here; the rest are covered by the static pins above.
# ---------------------------------------------------------------------------


async def _make_agreement(db: AsyncSession, org, user) -> Agreement:
    """Agreement with one version, one party (+ entity) and one participant."""
    atype = AgreementType(
        key=f"eager_{uuid.uuid4().hex[:8]}",
        name="Eager Test Type",
        category="test",
        schema={"questions": [], "clauses": []},
    )
    db.add(atype)
    await db.flush()

    entity = LegalEntity(
        organization_id=org.id,
        legal_name="Eager Test Entity",
        country="US",
    )
    db.add(entity)
    await db.flush()

    agreement = Agreement(
        organization_id=org.id,
        agreement_type_id=atype.id,
        title="Eager loading test",
        status="draft",
        created_by=user.id,
        data={},
    )
    db.add(agreement)
    await db.flush()

    db.add(
        type(agreement).versions.property.mapper.class_(
            agreement_id=agreement.id,
            version_number=1,
            content="v1",
            content_hash="hash-eager-test",
            created_by=user.id,
        )
    )
    party = AgreementParty(
        agreement_id=agreement.id,
        legal_entity_id=entity.id,
        party_role="disclosing",
    )
    db.add(party)
    await db.flush()

    from app.models.agreement_access import AgreementParticipant

    db.add(
        AgreementParticipant(
            agreement_id=agreement.id,
            agreement_party_id=party.id,
            user_id=user.id,
            participant_role="signatory",
        )
    )
    await db.commit()
    return agreement


class TestHotCollectionsLoadEagerly:
    async def test_agreement_collections_load_on_fresh_session(
        self, db_session, test_org, test_user, engine
    ):
        agreement = await _make_agreement(db_session, test_org, test_user)

        # Fresh session, fresh fetch — exactly what a request-scoped
        # serializer sees.
        maker = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        async with maker() as s2:
            row = (
                await s2.execute(select(Agreement).where(Agreement.id == agreement.id))
            ).scalar_one()
            # Synchronous access, no awaits: with lazy="select" this raises
            # MissingGreenlet; selectin loaded everything during the fetch.
            assert len(row.versions) == 1
            assert len(row.parties) == 1
            assert len(row.participants) == 1
            assert row.parties[0].display_name is None
            assert row.participants[0].participant_role == "signatory"

    async def test_forecast_predictions_load_on_fresh_session(
        self, db_session, test_org, engine
    ):
        run = ForecastRun(
            organization_id=test_org.id,
            metric_key="lifecycle.renewals_due_30d",
            model_type="trend",
            status="completed",
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(
            ForecastPrediction(
                run_id=run.id,
                target_date=__import__("datetime").date(2026, 12, 31),
                predicted_value=42.0,
            )
        )
        await db_session.commit()

        maker = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        async with maker() as s2:
            row = (
                await s2.execute(select(ForecastRun).where(ForecastRun.id == run.id))
            ).scalar_one()
            # The get-run serializer reads predictions right after fetch /
            # refresh — must already be loaded.
            assert len(row.predictions) == 1
            assert row.predictions[0].predicted_value == 42.0
