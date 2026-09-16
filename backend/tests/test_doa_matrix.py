"""Tests for the dynamic Delegation of Authority matrix (spec 24.2)."""

import pytest
from sqlalchemy import select

from app.models.approval import ApprovalDefinition, ApprovalStage
from app.services.doa_service import (
    DoaMatrixError,
    FALLBACK_MATRIX,
    create_matrix,
    list_matrices,
    resolve_doa_matrix,
)


@pytest.mark.asyncio
async def test_fallback_matrix_applies_when_no_db_rules(db_session, test_org):
    # No DB matrices configured -> built-in LKR thresholds apply.
    matrix = await resolve_doa_matrix(
        db_session,
        organization_id=test_org.id,
        agreement_value=20_000_000,
        currency="LKR",
    )
    assert matrix["source"] == "fallback"
    roles = [a["role"] for a in matrix["required_approvals"]]
    assert "ceo" in roles and "cfo" in roles and "legal" in roles

    low = await resolve_doa_matrix(
        db_session,
        organization_id=test_org.id,
        agreement_value=500_000,
        currency="LKR",
    )
    assert low["required_approvals"] == []


@pytest.mark.asyncio
async def test_db_matrix_overrides_fallback(db_session, test_org):
    definition = await create_matrix(
        db_session,
        test_org.id,
        name="Vendor contracts",
        description="Parallel VP + CFO above $50k",
        min_value=50_000,
        max_value=None,
        stages=[
            {
                "name": "VP Finance",
                "required_role": "vp_finance",
                "execution_mode": "parallel",
                "require_all_approvers": True,
            },
            {
                "name": "CFO",
                "required_role": "cfo",
                "execution_mode": "parallel",
                "require_all_approvers": True,
            },
        ],
    )
    assert definition.id is not None

    matrix = await resolve_doa_matrix(
        db_session,
        organization_id=test_org.id,
        agreement_value=75_000,
        currency="USD",
    )
    assert matrix["source"] == "database"
    assert matrix["definition_id"] == str(definition.id)
    assert {a["role"] for a in matrix["required_approvals"]} == {
        "vp_finance",
        "cfo",
    }
    # Value bands are compared in a common currency: 75k USD > 50k USD band.
    assert all(
        a["execution_mode"] == "parallel" for a in matrix["required_approvals"]
    )


@pytest.mark.asyncio
async def test_value_banding_selects_definition(db_session, test_org):
    await create_matrix(
        db_session,
        test_org.id,
        name="Small deals",
        min_value=0,
        max_value=10_000,
        stages=[
            {
                "name": "Manager",
                "required_role": "manager",
                "execution_mode": "sequential",
            }
        ],
    )
    await create_matrix(
        db_session,
        test_org.id,
        name="Large deals",
        min_value=100_000,
        max_value=None,
        stages=[
            {
                "name": "Board",
                "required_role": "board",
                "execution_mode": "parallel",
            }
        ],
    )

    small = await resolve_doa_matrix(
        db_session,
        organization_id=test_org.id,
        agreement_value=5_000,
        currency="USD",
    )
    assert small["name"] == "Small deals"

    large = await resolve_doa_matrix(
        db_session,
        organization_id=test_org.id,
        agreement_value=1_000_000,
        currency="USD",
    )
    assert large["name"] == "Large deals"


@pytest.mark.asyncio
async def test_invalid_execution_mode_rejected(db_session, test_org):
    with pytest.raises(DoaMatrixError):
        await create_matrix(
            db_session,
            test_org.id,
            name="Bad matrix",
            min_value=0,
            max_value=None,
            stages=[
                {
                    "name": "Whatever",
                    "required_role": "owner",
                    "execution_mode": "diagonal",
                }
            ],
        )


@pytest.mark.asyncio
async def test_list_matrices(db_session, test_org):
    await create_matrix(
        db_session,
        test_org.id,
        name="Standard",
        min_value=10_000,
        max_value=None,
        stages=[
            {
                "name": "Finance",
                "required_role": "finance",
                "execution_mode": "sequential",
            }
        ],
    )
    matrices = await list_matrices(db_session, test_org.id)
    assert len(matrices) == 1
    assert matrices[0]["stages"][0]["execution_mode"] == "sequential"


def test_fallback_matrix_shape():
    assert isinstance(FALLBACK_MATRIX, list)
    assert any("parallel" in [s["execution_mode"] for s in t["stages"]] for t in FALLBACK_MATRIX)