"""Regression tests for render_agreement content immutability (F2).

render_agreement must auto-fill content only on freshly-created (empty)
versions. It must never clobber a version that already carries text —
template-generated, negotiated, or composed — and must never touch a locked
version.
"""
import pytest_asyncio
from sqlalchemy import select

from app.models.agreement import Agreement, AgreementVersion
from app.models.agreement_type import AgreementType
from app.services.agreement_renderer import render_agreement


@pytest_asyncio.fixture
async def renderer_setup(db_session, test_org, test_user):
    atype = AgreementType(
        key="contract_test",
        name="Contract Test",
        description="Renderer regression fixture",
        category="other",
        template_key="generic_agreement_lk_v1",
        schema={"questions": [], "clauses": []},
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)

    agreement = Agreement(
        organization_id=test_org.id,
        agreement_type_id=atype.id,
        title="Renderer Regression Agreement",
        status="draft",
        created_by=test_user.id,
        data={},
    )
    db_session.add(agreement)
    await db_session.commit()
    await db_session.refresh(agreement)
    return agreement, test_user


async def _latest(db_session, agreement_id):
    result = await db_session.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


class TestRendererContentProtection:
    async def test_negotiated_content_not_clobbered(
        self, db_session, renderer_setup
    ):
        agreement, user = renderer_setup
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            content="NEGOTIATED TEXT THAT MUST SURVIVE",
            content_hash="negotiated-hash",
            status="current",
            created_by=user.id,
        )
        db_session.add(version)
        await db_session.commit()

        result = await render_agreement(
            db_session, agreement_id=agreement.id, generate_pdf=False
        )

        reloaded = await _latest(db_session, agreement.id)
        assert reloaded.content == "NEGOTIATED TEXT THAT MUST SURVIVE"
        assert reloaded.content_hash == "negotiated-hash"
        assert result.rendered_text != reloaded.content

    async def test_empty_version_gets_autofilled(
        self, db_session, renderer_setup
    ):
        agreement, user = renderer_setup
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            content="",
            content_hash="",
            status="draft",
            created_by=user.id,
        )
        db_session.add(version)
        await db_session.commit()

        result = await render_agreement(
            db_session, agreement_id=agreement.id, generate_pdf=False
        )

        reloaded = await _latest(db_session, agreement.id)
        assert reloaded.content != ""
        assert reloaded.content == result.rendered_text

    async def test_locked_version_never_rewritten(
        self, db_session, renderer_setup
    ):
        agreement, user = renderer_setup
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            content="",
            content_hash="",
            status="locked",
            created_by=user.id,
        )
        db_session.add(version)
        await db_session.commit()

        await render_agreement(
            db_session, agreement_id=agreement.id, generate_pdf=False
        )

        reloaded = await _latest(db_session, agreement.id)
        assert reloaded.content == ""
        assert reloaded.content_hash == ""