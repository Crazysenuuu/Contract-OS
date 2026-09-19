"""Tests for the supplier / financial-obligation risk analysis (spec 19).

Covers the explainable rules engine ``analyze_supplier_risk`` (financial and
supplier-dependence clusters with a weighted composite score) and the
``GET /api/v1/phase4/supplier-risk/{agreement_id}`` endpoint that wraps it.
"""
import uuid
import pytest

from app.services.supplier_risk_service import (
    analyze_supplier_risk,
    get_supplier_risk,
)

RISK_ANSWERS_EXECUTED = {
    "late_fee_percentage": "30",
    "interest_rate": "32",
    "credit_limit": "500000",
    "currency": "USD",
    "collateral_description": "All inventory & receivable collateral",
    "guarantee_mechanism": "Parent-company guarantee",
    "governing_law": "New York",
    "single_source_supplier": "yes",
    "exclusivity": "exclusive",
    "escalation_rate": "12",
}


def test_analyze_supplier_risk_flags_financial_and_dependence(
    db_session, test_org, test_agreement
):
    # analyze_supplier_risk is a synchronous, DB-free rules engine: feed it a
    # small fake with only the answers dict so no schema lookup is required.
    class _FakeAgreement:
        def __init__(self, data):
            self.data = data
            self.currency = data.get("currency")
            self.id = uuid.uuid4()
            self.value = float(data.get("credit_limit") or 0)

    profile = analyze_supplier_risk(None, _FakeAgreement(RISK_ANSWERS_EXECUTED))
    d = profile.to_dict()

    assert d["overall"] in {"low", "medium", "high"}
    assert isinstance(d["score"], float)
    kinds = {f["kind"] for f in d["factors"]}
    assert "aggressive_late_fee" in kinds
    assert "high_interest_rate" in kinds
    assert "single_source_dependence" in kinds


def test_analyze_supplier_risk_low_when_no_obligations(db_session, test_agreement):
    class _FakeAgreement:
        def __init__(self, data):
            self.data = data
            self.currency = None
            self.value = 0.0

    profile = analyze_supplier_risk(None, _FakeAgreement({}))
    d = profile.to_dict()

    assert d["overall"] == "low"
    assert d["score"] == 0.0
    assert d["factors"] == []
    assert d["obligations"] == {}


async def test_get_supplier_risk_service(db_session, test_agreement):
    from app.models.agreement import Agreement

    profile = await get_supplier_risk(db_session, test_agreement.id)
    d = profile.to_dict()
    assert d["agreement_id"] == str(test_agreement.id)
    assert isinstance(d["score"], float)
    assert d["category_risks"]["supplier_dependence"]["level"] in {
        "low",
        "medium",
        "high",
    }


async def test_get_supplier_risk_endpoint(
    client, auth_headers, db_session, test_agreement
):
    resp = await client.get(
        f"/api/v1/phase4/supplier-risk/{test_agreement.id}", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agreement_id"] == str(test_agreement.id)
    assert body["overall"] in {"low", "medium", "high"}
    assert isinstance(body["score"], float)
    assert isinstance(body["category_risks"], dict)
    assert isinstance(body["factors"], list)
