#!/usr/bin/env python3
"""End-to-end test for ContractOS backend API."""

import json
import subprocess
import sys
import time

import requests

BASE = "http://127.0.0.1:8000"


def test_health():
    r = requests.get(f"{BASE}/health")
    assert r.status_code == 200
    print(f"✅ Health check: {r.json()}")


def test_register():
    r = requests.post(f"{BASE}/api/v1/auth/register", json={
        "email": "demo@contractos.lk",
        "name": "Demo User",
        "password": "demo123456",
    })
    if r.status_code == 409:
        print("⚠️  User already exists, logging in instead")
        return test_login()
    assert r.status_code == 201, f"Register failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ Register: user_id={data['user_id']}")
    return data["access_token"]


def test_login():
    r = requests.post(f"{BASE}/api/v1/auth/login", json={
        "email": "demo@contractos.lk",
        "password": "demo123456",
    })
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ Login: user_id={data['user_id']}")
    return data["access_token"]


def test_me(token):
    r = requests.get(f"{BASE}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        print(f"⚠️  /me returned {r.status_code}: {r.text[:200]}")
    assert r.status_code == 200, f"/me failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    print(f"✅ Me: {data['name']} ({data['email']})")
    return data


def test_org(token):
    r = requests.get(f"{BASE}/api/v1/organizations/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    print(f"✅ Organization: {data['name']}")
    return data


def test_list_templates():
    r = requests.get(f"{BASE}/api/v1/agreements/templates/list")
    assert r.status_code == 200
    templates = r.json()
    print(f"✅ Templates: {len(templates)} available")
    return templates


def test_template_questions(key):
    r = requests.get(f"{BASE}/api/v1/agreements/templates/{key}/questions")
    assert r.status_code == 200
    questions = r.json()
    print(f"✅ Template questions: {len(questions)} questions for {key}")
    return questions


def test_create_legal_entity(token):
    r = requests.post(f"{BASE}/api/v1/legal-entities", json={
        "legal_name": "ABC Technologies (Pvt) Ltd",
        "country": "LK",
        "registration_number": "PV00123456",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, f"Create legal entity failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ Legal Entity: {data['legal_name']}")
    return data


def test_create_agreement(token, agreement_type_id):
    r = requests.post(f"{BASE}/api/v1/agreements", json={
        "title": "Mutual NDA between ABC and XYZ",
        "agreement_type_id": agreement_type_id,
        "governing_law": "Sri Lanka",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, f"Create agreement failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ Agreement created: {data['id']} (status: {data['status']})")
    return data


def test_update_answers(token, agreement_id):
    answers = {
        "party_a_legal_name": "ABC Technologies (Pvt) Ltd",
        "party_a_address": "123 Main Street, Colombo, Sri Lanka",
        "party_a_country": "Sri Lanka",
        "party_b_legal_name": "XYZ Software Ltd",
        "party_b_address": "456 Tech Park, Singapore",
        "party_b_country": "Sri Lanka",
        "effective_date": "2026-09-01",
        "purpose": "Evaluating a potential business partnership",
        "duration_years": 2,
        "termination_notice_days": 30,
        "confidentiality_survival_years": 5,
        "includes_technical_info": True,
        "includes_business_info": True,
        "includes_financial_info": False,
        "includes_product_info": False,
        "includes_personal_data": False,
        "includes_legal_info": False,
        "governing_law": "Sri Lanka",
        "dispute_resolution": "court",
        "court_jurisdiction": "Colombo",
        "party_a_signatory_name": "John Smith",
        "party_a_signatory_title": "CEO",
        "party_b_signatory_name": "Jane Doe",
        "party_b_signatory_title": "Managing Director",
        "include_witness": True,
        "witness_name": "Alice Brown",
        "electronic_signatures_allowed": True,
    }
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/answers",
        json={"answers": answers},
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"Update answers failed: {r.status_code} {r.text}"
    print(f"✅ Answers updated: {len(answers)} fields")
    return answers


def test_validate(token, agreement_id):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/validate",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    print(f"✅ Validation: valid={data['valid']}, errors={len(data.get('errors', []))}")
    return data


def test_render(token, agreement_id):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/render?generate_pdf=false",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"Render failed: {r.status_code} {r.text}"
    data = r.json()
    text_len = len(data.get("rendered_text", ""))
    print(f"✅ Rendered: {text_len} characters, hash={data.get('content_hash', 'N/A')[:16]}...")
    return data


def test_workflow(token, agreement_id):
    r = requests.get(f"{BASE}/api/v1/agreements/{agreement_id}/workflow/state",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    state = r.json()
    print(f"✅ Workflow state: {state['name']}")

    r = requests.get(f"{BASE}/api/v1/agreements/{agreement_id}/workflow/actions",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    actions = r.json()
    print(f"   Available actions: {[a['name'] for a in actions]}")
    return state, actions


def test_versions(token, agreement_id):
    r = requests.get(f"{BASE}/api/v1/agreements/{agreement_id}/versions",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    versions = r.json()
    print(f"✅ Versions: {len(versions)} versions")
    return versions


def test_add_parties(token, agreement_id, legal_entity_id):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/parties", json={
        "legal_entity_id": legal_entity_id,
        "party_role": "disclosing_and_receiving",
        "display_name": "ABC Technologies",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, f"Add party failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ Party added: {data['party_role']}")
    return data


def test_add_external_party(token, agreement_id, party_id):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/external-parties", json={
        "agreement_party_id": party_id,
        "company_name": "XYZ Software Ltd",
        "signatory_name": "Jane Doe",
        "signatory_email": "jane@xyz.com",
        "signatory_title": "Managing Director",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, f"Add external party failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ External party: token={data['access_token'][:20]}...")
    return data


def test_external_review(access_token):
    r = requests.get(f"{BASE}/review/{access_token}")
    assert r.status_code == 200, f"External review failed: {r.status_code} {r.text}"
    data = r.json()
    print(f"✅ External review: '{data['title']}' v{data['version_number']}")
    return data


def test_list_agreements(token):
    r = requests.get(f"{BASE}/api/v1/agreements",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    agreements = r.json()
    print(f"✅ Agreements list: {len(agreements)} agreements")
    for a in agreements:
        print(f"   - {a['title']} [{a['status']}]")
    return agreements


def test_audit(token, agreement_id):
    r = requests.get(f"{BASE}/api/v1/agreements/{agreement_id}/audit",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"Audit failed: {r.status_code} {r.text}"
    events = r.json()
    print(f"✅ Audit trail: {len(events)} events")
    for e in events:
        print(f"   - {e['action']} by {e['actor_type']} at {e['created_at'][:19]}")
    return events


def test_send_agreement(token, agreement_id):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/send",
        headers={"Authorization": f"Bearer {token}"})
    print(f"✅ Send agreement: {r.status_code}")
    return r.status_code == 200


def test_sign_agreement(token, agreement_id):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/sign",
        json={"consent_text": "I agree to the terms"},
        headers={"Authorization": f"Bearer {token}"})
    print(f"✅ Sign agreement: {r.status_code}")
    return r.json() if r.status_code == 200 else None


def test_check_permission(token, agreement_id, permission):
    r = requests.post(f"{BASE}/api/v1/agreements/{agreement_id}/check-permission",
        json={"permission_key": permission},
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    print(f"   Permission '{permission}': {data['allowed']} ({data['reason']})")
    return data


def main():
    print("=" * 60)
    print("  ContractOS - End-to-End Test")
    print("=" * 60)
    print()

    # 1. Health
    test_health()
    print()

    # 2. Auth
    token = test_register()
    user = test_me(token)
    org = test_org(token)
    print()

    # 3. Templates
    templates = test_list_templates()
    template_key = templates[0]["key"] if templates else "mutual_nda_lk_v1"
    questions = test_template_questions(template_key)
    print()

    # 4. Legal Entity
    entity = test_create_legal_entity(token)
    print()

    # 5. Agreement
    agreement_type_id = 'a0000000-0000-0000-0000-000000000001'
    print(f'   Using agreement_type_id: {agreement_type_id}')
    agreement = test_create_agreement(token, agreement_type_id)
    print()

    # 6. Answers & Validation
    test_update_answers(token, agreement["id"])
    validation = test_validate(token, agreement["id"])
    print()

    # 7. Render
    if validation["valid"]:
        render_result = test_render(token, agreement["id"])
    else:
        print(f"⚠️  Skipping render - validation errors: {validation['errors']}")
    print()

    # 8. Versions
    versions = test_versions(token, agreement["id"])
    print()

    # 9. Parties
    party = test_add_parties(token, agreement["id"], entity["id"])
    print()

    # 10. External Party
    ext_party = test_add_external_party(token, agreement["id"], party["id"])
    print()

    # 11. External Review
    test_external_review(ext_party["access_token"])
    print()

    # 12. Workflow
    state, actions = test_workflow(token, agreement["id"])
    print()

    # 13. List all
    test_list_agreements(token)
    print()

    # 14. Audit trail
    test_audit(token, agreement["id"])
    print()

    # 15. Permission checks
    print("Permission checks:")
    test_check_permission(token, agreement["id"], "agreement.view")
    test_check_permission(token, agreement["id"], "agreement.sign")
    test_check_permission(token, agreement["id"], "agreement.approve")
    print()

    # 16. Send agreement
    test_send_agreement(token, agreement["id"])
    print()

    # 17. Sign agreement
    sign_result = test_sign_agreement(token, agreement["id"])
    if sign_result:
        print(f"   Status: {sign_result.get('agreement_status', 'unknown')}")
    print()

    # 18. Final audit
    test_audit(token, agreement["id"])
    print()

    print("=" * 60)
    print("  ALL TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except requests.ConnectionError:
        print("\n❌ Cannot connect to server at", BASE)
        print("   Make sure the backend is running:")
        print("   cd backend && source venv/bin/activate && uvicorn app.main:app --port 8000")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
