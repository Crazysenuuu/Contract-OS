"""Approval step-up authentication tests (spec 2.05.18).

MFA-enabled approvers must present a valid TOTP code with a decision;
missing or wrong codes are rejected before any state changes.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException


def _mfa_user():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        mfa_enabled=True,
        mfa_secret="JBSWY3DPEHPK3PXP",
    )


@pytest.mark.asyncio
async def test_missing_mfa_code_is_rejected(db_session, test_agreement, test_user):
    """An MFA-enabled user without a code gets 403 + mfa_required header."""
    import app.api.v1.approvals as approvals_api

    class _Req:
        decision = "approved"
        mfa_code = None

    data = _Req()

    async def _noop(*args, **kwargs):
        return test_agreement

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        request = approvals_api.make_decision(
            agreement_id=test_agreement.id,
            record_id="00000000-0000-0000-0000-000000000009",
            data=data,
            current_user=_mfa_user(),
            org_id=test_agreement.organization_id,
            db=db_session,
        )
        with pytest.raises(HTTPException) as exc:
            await request
    assert exc.value.status_code == 403
    assert "mfa_required" in str(exc.value.headers)


@pytest.mark.asyncio
async def test_wrong_mfa_code_is_rejected(db_session, test_agreement):
    import app.api.v1.approvals as approvals_api

    class _Req:
        decision = "approved"
        mfa_code = "000000"

    async def _noop(*args, **kwargs):
        return test_agreement

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        with pytest.raises(HTTPException) as exc:
            await approvals_api.make_decision(
                agreement_id=test_agreement.id,
                record_id="00000000-0000-0000-0000-000000000009",
                data=_Req(),
                current_user=_mfa_user(),
                org_id=test_agreement.organization_id,
                db=db_session,
            )
    assert exc.value.status_code == 403
    assert "Invalid MFA" in exc.value.detail


@pytest.mark.asyncio
async def test_valid_mfa_code_passes_step_up_gate(db_session, test_agreement):
    """A valid TOTP gets past the step-up gate (then hits record-not-found,
    proving the gate is not the failure point)."""
    import pyotp

    import app.api.v1.approvals as approvals_api

    class _Req:
        decision = "approved"
        mfa_code = pyotp.TOTP("JBSWY3DPEHPK3PXP").now()

    async def _noop(*args, **kwargs):
        return test_agreement

    with patch.object(approvals_api, "verify_agreement_access", _noop):
        with pytest.raises(HTTPException) as exc:
            await approvals_api.make_decision(
                agreement_id=test_agreement.id,
                record_id="00000000-0000-0000-0000-000000000009",
                data=_Req(),
                current_user=_mfa_user(),
                org_id=test_agreement.organization_id,
                db=db_session,
            )
    # Past step-up; failed only because that approval record doesn't exist.
    assert exc.value.status_code == 404
