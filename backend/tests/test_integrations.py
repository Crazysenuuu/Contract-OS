"""Tests for ERP/CRM/Stripe integration connectors (spec 24.6)."""

import pytest
from sqlalchemy import select

from app.models.integration import IntegrationConnector
from app.services.integration_service import (
    build_payload,
    dispatch_event,
    sign_payload,
)


class _FakeAgreement:
    def __init__(self, agreement_id=None):
        import uuid

        self.id = agreement_id or uuid.uuid4()
        self.title = "Acme Master Agreement"
        self.status = "executed"
        self.governing_law = "New York"
        self.effective_date = None
        self.expiry_date = None
        self.value = 25000.0
        self.currency = "USD"


def test_salesforce_payload_shape():
    payload = build_payload("salesforce", "agreement.signed", _FakeAgreement())
    assert payload["sobject"] == "Contract"
    assert payload["fields"]["Name"] == "Acme Master Agreement"
    assert payload["fields"]["Status"] == "executed"


def test_hubspot_payload_shape():
    payload = build_payload("hubspot", "agreement.signed", _FakeAgreement())
    assert payload["objectType"] == "DEAL"
    props = {p["name"]: p["value"] for p in payload["properties"]}
    assert props["dealname"] == "Acme Master Agreement"


def test_netsuite_and_sap_payloads():
    ns = build_payload("netsuite", "agreement.executed", _FakeAgreement())
    assert ns["recordType"] == "contract"
    assert ns["externalId"] == str(ns["externalId"])
    assert ns["fields"]["custrecord_contract_title"]

    sap = build_payload("sap", "agreement.terminated", _FakeAgreement())
    assert sap["operation"] == "TERMINATED"
    assert sap["contract"]["ContractID"]


def test_stripe_payload_minor_units():
    payload = build_payload("stripe", "payment.recorded", _FakeAgreement())
    assert payload["amount"] == 2500000  # 25,000 USD * 100


def test_signature_is_deterministic_hmac():
    payload = build_payload("salesforce", "agreement.signed", _FakeAgreement())
    sig1 = sign_payload(payload, "secret")
    sig2 = sign_payload(payload, "secret")
    assert sig1 == sig2
    assert sign_payload(payload, "other") != sig1


@pytest.mark.asyncio
async def test_dispatch_event_only_to_subscribed_connectors(
    db_session, test_org, test_agreement
):
    db_session.add(
        IntegrationConnector(
            organization_id=test_org.id,
            provider="salesforce",
            name="Salesforce",
            events=["agreement.signed", "agreement.executed"],
            enabled=True,
        )
    )
    db_session.add(
        IntegrationConnector(
            organization_id=test_org.id,
            provider="hubspot",
            name="HubSpot",
            events=["obligation.due"],
            enabled=True,
        )
    )
    await db_session.flush()

    outcomes = await dispatch_event(
        db_session,
        organization_id=test_org.id,
        event="agreement.signed",
        agreement=test_agreement,
    )
    providers = {o["provider"] for o in outcomes}
    assert "salesforce" in providers
    assert "hubspot" not in providers  # not subscribed to agreement.signed

    # Salesforce connector records a successful sync.
    result = await db_session.execute(
        select(IntegrationConnector).where(
            IntegrationConnector.provider == "salesforce"
        )
    )
    connector = result.scalar_one()
    assert connector.last_sync_status == "success"


@pytest.mark.asyncio
async def test_dispatch_skips_disabled_connectors(db_session, test_org, test_agreement):
    db_session.add(
        IntegrationConnector(
            organization_id=test_org.id,
            provider="stripe",
            name="Stripe",
            events=["agreement.signed"],
            enabled=False,
        )
    )
    await db_session.flush()

    outcomes = await dispatch_event(
        db_session,
        organization_id=test_org.id,
        event="agreement.signed",
        agreement=test_agreement,
    )
    assert outcomes == []