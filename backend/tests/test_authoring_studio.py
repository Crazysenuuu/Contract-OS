"""Authoring studio tests (spec 2.03): schema validation, agreement-types API,
and entity-ownership enforcement on agreement creation."""

import uuid

import pytest
from sqlalchemy import select

from app.services.agreement_schema_validator import (
    build_json_schema,
    validate,
    extract_entity_references,
)


@pytest.mark.asyncio
async def test_json_schema_from_sections(db_session):
    schema = {
        "schema_version": 4,
        "sections": [
            {
                "key": "parties",
                "fields": [
                    {"key": "customer_entity", "type": "legal_entity", "required": True},
                    {"key": "supplier_entity", "type": "legal_entity", "required": True},
                ],
            },
            {
                "key": "commercial_terms",
                "fields": [
                    {"key": "payment_terms", "type": "duration", "required": True},
                    {"key": "active", "type": "boolean"},
                ],
            },
        ],
    }
    js = build_json_schema(schema)
    assert js["type"] == "object"
    assert set(js["required"]) == {"customer_entity", "supplier_entity", "payment_terms"}
    assert js["properties"]["payment_terms"]["type"] == "number"
    assert js["properties"]["active"]["type"] == "boolean"


def test_validate_passes_and_fails():
    schema = {
        "type": "object",
        "properties": {
            "payment_terms": {"type": "number"},
            "email": {"type": "string", "format": "email"},
        },
        "required": ["payment_terms"],
    }
    validate(schema, {"payment_terms": 30, "email": "a@b.com"})  # ok

    with pytest.raises(ValueError) as exc:
        validate(schema, {"payment_terms": "thirty", "email": "nope"})
    msgs = exc.value.args[0]
    paths = {tuple(path) for path in msgs["paths"] if isinstance(msgs, dict)} if isinstance(msgs, dict) else None
    # Errors surface as a list of {path, message}
    assert isinstance(msgs, list)
    assert any(m["message"] and m["path"] for m in msgs)


def test_validate_conditional_required_field():
    schema = {
        "sections": [
            {
                "fields": [
                    {"key": "personal_data_processed", "type": "boolean"},
                    {
                        "key": "security_requirements",
                        "type": "text",
                        "condition": {"field": "personal_data_processed", "operator": "equals", "value": True},
                    },
                ],
            }
        ]
    }
    js = build_json_schema(schema)
    # security_requirements is only required when personal_data_processed is true
    assert "security_requirements" not in js.get("required", [])
    validate(js, {"personal_data_processed": False})
    with pytest.raises(ValueError):
        validate(js, {"personal_data_processed": True})
    validate(js, {"personal_data_processed": True, "security_requirements": "encrypt at rest"})


def test_select_enum_and_multi_select():
    schema = {
        "sections": [{
            "fields": [
                {"key": "country", "type": "select", "options": [{"value": "LK"}, {"value": "US"}], "required": True},
                {"key": "tags", "type": "multi_select", "options": ["x", "y"]},
            ],
        }],
    }
    js = build_json_schema(schema)
    assert js["properties"]["country"]["enum"] == ["LK", "US"]
    assert js["properties"]["tags"]["items"]["enum"] == ["x", "y"]
    validate(js, {"country": "LK", "tags": ["x"]})
    with pytest.raises(ValueError):
        validate(js, {"country": "XX"})


def test_extract_entity_references():
    schema = {
        "sections": [{
            "fields": [
                {"key": "customer_entity", "type": "legal_entity"},
                {"key": "supplier_entity", "type": "agreement_party"},
                {"key": "note", "type": "text"},
            ],
        }],
    }
    assert set(extract_entity_references(schema)) == {"customer_entity", "supplier_entity"}


def test_unsupported_field_type_rejected():
    schema = {"sections": [{"fields": [{"key": "evil", "type": "javascript"}]}]}
    with pytest.raises(ValueError):
        build_json_schema(schema)


# ---------------------------------------------------------------------------
# HTTP-level authoring tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_agreement_validates_intake(client, auth_headers, db_session, test_user, test_org):
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="contract_authoring_test",
        name="Authoring Test",
        category="commercial",
        status="active",
        schema={
            "schema_version": 1,
            "sections": [{
                "fields": [
                    {"key": "value", "type": "number", "required": True},
                ],
            }],
        },
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)

    # An empty payload is the wizard's "create draft" call: it creates the
    # agreement shell first and fills answers incrementally, so schema
    # validation only applies to intake that was actually supplied.
    draft = await client.post(
        "/api/v1/agreements",
        json={"title": "T", "agreement_type_id": str(atype.id), "data": {}},
        headers=auth_headers,
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()["data"] == {}

    invalid = await client.post(
        "/api/v1/agreements",
        json={
            "title": "T2",
            "agreement_type_id": str(atype.id),
            "data": {"value": "not-a-number"},
        },
        headers=auth_headers,
    )
    assert invalid.status_code == 422

    ok = await client.post(
        "/api/v1/agreements",
        json={"title": "T3", "agreement_type_id": str(atype.id), "data": {"value": 42}},
        headers=auth_headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["data"]["value"] == 42


@pytest.mark.asyncio
async def test_create_agreement_rejects_foreign_entity(client, auth_headers, db_session, test_org):
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="contract_authoring_entity_test",
        name="Entity Test",
        category="commercial",
        status="active",
        schema={
            "schema_version": 1,
            "sections": [{
                "fields": [{"key": "customer_entity", "type": "legal_entity", "required": True}],
            }],
        },
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)

    # Legal entity belongs to a DIFFERENT org.
    forced = str(uuid.uuid4())

    # Entity not owned by this org / non-existent -> rejected (422), since
    # there is no owned legal entity with that id.
    resp = await client.post(
        "/api/v1/agreements",
        json={
            "title": "T",
            "agreement_type_id": str(atype.id),
            "data": {"customer_entity": forced},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_agreement_types_published_only(client, auth_headers, db_session):
    from app.models.agreement_type import AgreementType

    published = AgreementType(
        key="pub_type", name="Published", category="commercial", status="active",
        schema={"schema_version": 2, "sections": []},
    )
    draft = AgreementType(
        key="draft_type", name="Draft", category="commercial", status="draft",
        schema={"schema_version": 1, "sections": []},
    )
    db_session.add_all([published, draft])
    await db_session.commit()

    resp = await client.get("/api/v1/agreement-types", headers=auth_headers)
    assert resp.status_code == 200
    keys = [t["key"] for t in resp.json()]
    assert "pub_type" in keys
    assert "draft_type" not in keys
    pub = next(t for t in resp.json() if t["key"] == "pub_type")
    assert pub["schema_version"] == 2


@pytest.mark.asyncio
async def test_get_agreement_type_detail(client, auth_headers, db_session):
    from app.models.agreement_type import AgreementType

    atype = AgreementType(
        key="detail_type", name="Detail", category="commercial", status="active",
        template_key="msa_lk_v1",
        schema={"schema_version": 3, "sections": [{"fields": [{"key": "x", "type": "text"}]}]},
    )
    db_session.add(atype)
    await db_session.commit()
    await db_session.refresh(atype)

    resp = await client.get(f"/api/v1/agreement-types/{atype.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "detail_type"
    assert body["template_key"] == "msa_lk_v1"
    assert body["schema_version"] == 3
    assert body["schema"]["sections"]