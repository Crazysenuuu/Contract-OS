"""Smoke test: the lifecycle/agreement seed data applies cleanly and idempotently."""
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestSeedLifecycle:
    @pytest_asyncio.fixture(autouse=True)
    def setup(self, db_session):
        self.db = db_session

    async def _count(self, model):
        from sqlalchemy import func, select
        return (await self.db.execute(select(func.count()).select_from(model))).scalar()

    async def test_seed_lifecycle_and_types(self):
        from seed_data import (
            AGREEMENT_STATES,
            AGREEMENT_TYPES,
            TRANSITION_RULES,
            seed_extended_agreement_types,
            seed_lifecycle,
        )
        # Runs once, then again to confirm idempotency.
        await seed_lifecycle(self.db)
        await seed_extended_agreement_types(self.db)
        await seed_lifecycle(self.db)
        await seed_extended_agreement_types(self.db)
        await self.db.commit()

        from app.models.agreement_type import AgreementType
        from app.models.lifecycle import AgreementState, StatusTransitionRule

        assert await self._count(AgreementState) == len(AGREEMENT_STATES)
        assert await self._count(StatusTransitionRule) == len(TRANSITION_RULES)
        assert await self._count(AgreementType) >= len(AGREEMENT_TYPES)

        from sqlalchemy import select
        mutual = (await self.db.execute(
            select(AgreementType).where(AgreementType.key == "mutual_nda")
        )).scalar_one()
        assert mutual.template_key == "mutual_nda_lk_v1"

    async def test_renderer_resolves_template_key_from_db(self):
        from datetime import date
        from sqlalchemy import select
        from app.models.agreement_type import AgreementType
        from seed_data import seed_extended_agreement_types

        await seed_extended_agreement_types(self.db)
        await self.db.commit()

        atype = (await self.db.execute(
            select(AgreementType).where(AgreementType.key == "mutual_nda")
        )).scalar_one()

        from app.models.agreement import Agreement
        agreement = Agreement(
            organization_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000099"),
            agreement_type_id=atype.id,
            title="test",
            status="draft",
            created_by=__import__("uuid").UUID("00000000-0000-0000-0000-000000000098"),
            data={"effective_date": str(date(2026, 9, 1))},
        )
        self.db.add(agreement)
        await self.db.flush()

        from app.services.agreement_renderer import resolve_template_key
        key = await resolve_template_key(self.db, agreement)
        assert key == "mutual_nda_lk_v1"

    async def test_new_types_have_template_keys(self):
        from seed_data import AGREEMENT_TYPES, seed_extended_agreement_types
        await seed_extended_agreement_types(self.db)
        await self.db.commit()
        import os
        template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
        for at in AGREEMENT_TYPES:
            tk = at.get("template_key")
            if not tk:
                continue
            assert os.path.exists(
                os.path.join(template_dir, f"{tk}.jinja2")
            ), f"{at['key']}: no template for {tk}"