"""Tests for execution evidence (spec 1.15 / 2.06).

Covers signature requests, signer records, execution requirements and the
sealed execution package.
"""
import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import (
    ExecutionPackage,
    SignatureRequest,
)
from app.services.execution_service import (
    check_execution_requirements,
    create_execution_requirement,
    create_signature_request,
    decline_signature_request,
    mark_request_sent,
    record_signer,
    satisfy_execution_requirement,
    seal_execution_package,
    verify_execution_package,
)


@pytest_asyncio.fixture
async def signature_request(db_session: AsyncSession, test_org, test_agreement, test_user):
    from app.services.agreement_versioning import get_latest_version
    from app.models.agreement import AgreementVersion

    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content="Test NDA v1 content",
        content_hash=uuid.uuid4().hex,
        created_by=test_user.id,
        status="approved",
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)

    request = await create_signature_request(
        db_session,
        tenant_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=version.id,
        name="Counterparty CEO",
        email="ceo@counterparty.com",
        role="signer",
        created_by=test_user.id,
    )
    await db_session.commit()
    request.version = version
    return request


class TestSignatureRequests:
    async def test_create_and_send(self, signature_request, db_session, test_org, test_user):
        assert signature_request.status == "pending"

        await mark_request_sent(
            db_session, test_org.id, signature_request, actor_id=test_user.id
        )
        await db_session.commit()
        assert signature_request.status == "sent"
        assert signature_request.sent_at is not None

    async def test_decline(self, signature_request, db_session, test_org, test_user):
        await decline_signature_request(
            db_session, test_org.id, signature_request,
            actor_id=test_user.id, reason="needs changes",
        )
        await db_session.commit()
        assert signature_request.status == "declined"

    async def test_record_signer_creates_evidence(self, signature_request, db_session, test_org, test_user):
        await mark_request_sent(
            db_session, test_org.id, signature_request, actor_id=test_user.id
        )
        record = await record_signer(
            db_session,
            tenant_id=test_org.id,
            signature_request=signature_request,
            name="Counterparty CEO",
            email="ceo@counterparty.com",
            consent_text="I agree to the terms",
            signer_type="external",
            identity_verified=True,
            identity_method="otp",
        )
        await db_session.commit()
        assert signature_request.status == "signed"
        assert record.signature_hash
        assert record.identity_verified is True


class TestExecutionRequirements:
    async def test_check_blocks_until_satisfied(self, db_session, test_org, test_agreement, test_user):
        await create_execution_requirement(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            requirement_type="signatory_authority",
            description="Signer must have board authority",
            created_by=test_user.id,
        )
        await db_session.commit()

        result = await check_execution_requirements(db_session, test_agreement.id)
        assert result["ready"] is False
        assert result["required_count"] == 1
        assert result["pending_count"] == 1

    async def test_satisfy_requirement(self, db_session, test_org, test_agreement, test_user):
        req = await create_execution_requirement(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            requirement_type="consent",
            description="Written consent required",
            created_by=test_user.id,
        )
        await db_session.commit()

        await satisfy_execution_requirement(
            db_session,
            tenant_id=test_org.id,
            requirement=req,
            actor_id=test_user.id,
        )
        await db_session.commit()

        result = await check_execution_requirements(db_session, test_agreement.id)
        assert result["ready"] is True
        assert result["satisfied_required_count"] == 1


class TestExecutionPackage:
    async def test_seal_and_verify(self, db_session, test_org, test_agreement, signature_request, test_user):
        # Sign, then seal the package.
        await mark_request_sent(
            db_session, test_org.id, signature_request, actor_id=test_user.id
        )
        await record_signer(
            db_session,
            tenant_id=test_org.id,
            signature_request=signature_request,
            name="Counterparty CEO",
            email="ceo@counterparty.com",
            consent_text="I agree to the terms",
        )
        await db_session.flush()

        package = await seal_execution_package(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=signature_request.version_id,
            final_document_hash="f" * 64,
            sealed_by=test_user.id,
        )
        await db_session.commit()

        assert package.status == "sealed"
        assert package.package_hash is not None
        # Signature + document_hash evidence items.
        assert len(package.items) == 2

        verification = await verify_execution_package(db_session, package)
        assert verification["valid"] is True

    async def test_cannot_seal_twice(self, db_session, test_org, test_agreement, test_user):
        import pytest

        await seal_execution_package(
            db_session,
            tenant_id=test_org.id,
            agreement_id=test_agreement.id,
            version_id=uuid.uuid4(),
            final_document_hash="g" * 64,
            sealed_by=test_user.id,
        )
        await db_session.flush()

        with pytest.raises(ValueError):
            await seal_execution_package(
                db_session,
                tenant_id=test_org.id,
                agreement_id=test_agreement.id,
                version_id=uuid.uuid4(),
                final_document_hash="h" * 64,
                sealed_by=test_user.id,
            )


class TestExecutionApi:
    async def test_signature_request_api(self, client, test_agreement, test_user, test_org, auth_headers, db_session):
        from app.models.agreement import AgreementVersion

        version = AgreementVersion(
            agreement_id=test_agreement.id,
            version_number=1,
            content="v1",
            content_hash=uuid.uuid4().hex,
            created_by=test_user.id,
            status="approved",
        )
        db_session.add(version)
        await db_session.commit()
        await db_session.refresh(version)

        response = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/signature-requests",
            headers=auth_headers,
            json={
                "name": "Counsel A",
                "email": "counsel@a.com",
                "version_id": str(version.id),
                "role": "signer",
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "pending"

        request_id = response.json()["id"]
        sent = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/signature-requests/{request_id}/send",
            headers=auth_headers,
        )
        assert sent.status_code == 200
        assert sent.json()["status"] == "sent"

    async def test_requirements_api(self, client, test_agreement, auth_headers):
        response = await client.post(
            f"/api/v1/agreements/{test_agreement.id}/execution/requirements",
            headers=auth_headers,
            json={
                "requirement_type": "signatory_authority",
                "description": "Board approval required",
            },
        )
        assert response.status_code == 201

        check = await client.get(
            f"/api/v1/agreements/{test_agreement.id}/execution/requirements/check",
            headers=auth_headers,
        )
        assert check.status_code == 200
        assert check.json()["ready"] is False
        assert check.json()["required_count"] == 1