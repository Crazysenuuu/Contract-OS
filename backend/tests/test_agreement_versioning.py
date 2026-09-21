"""Tests for agreement version control (spec §26 / §3).

Covers append-only version history for creating and modifying agreements:
snapshot on create, new version on answers update, view, compare, restore.
"""

import pytest


@pytest.fixture
async def versioned_agreement(db_session, test_agreement, test_user):
    """An agreement whose v1 snapshot mirrors its initial answers."""
    from app.services.agreement_versioning import create_version

    version = await create_version(
        db=db_session,
        agreement=test_agreement,
        content="",
        created_by=test_user.id,
        data=test_agreement.data,
        note="Initial version",
    )
    await db_session.commit()
    return test_agreement, version


@pytest.mark.asyncio
async def test_create_version_captures_immutable_snapshot(
    db_session, test_agreement, test_user
):
    from app.services.agreement_versioning import create_version

    original = {"effective_date": "2026-01-01", "term_years": 2}
    version = await create_version(
        db=db_session,
        agreement=test_agreement,
        content="rendered v1",
        created_by=test_user.id,
        data=original,
        note="Initial version",
    )
    await db_session.commit()

    assert version.version_number == 1
    assert version.note == "Initial version"
    assert version.data == original
    assert version.content_hash

    # Mutating the caller's dict must not rewrite the stored snapshot.
    original["term_years"] = 99
    await db_session.refresh(version)
    assert (version.data or {})["term_years"] == 2


@pytest.mark.asyncio
async def test_versions_are_append_only(db_session, versioned_agreement, test_user):
    from app.services.agreement_versioning import create_version, list_versions

    agreement, v1 = versioned_agreement
    v2 = await create_version(
        db=db_session,
        agreement=agreement,
        content="",
        created_by=test_user.id,
        data={"effective_date": "2026-02-02"},
        note="Answers updated",
    )
    await db_session.commit()

    assert v2.version_number == v1.version_number + 1
    versions = await list_versions(db_session, agreement.id)
    assert [v.version_number for v in versions] == [1, 2]
    # v1 is untouched.
    assert v1.data == agreement.data or v1.data == {}


@pytest.mark.asyncio
async def test_update_answers_appends_new_version(
    db_session, versioned_agreement, test_user
):
    from app.api.v1 import agreements as agreements_api

    agreement, v1 = versioned_agreement
    v1_data_before = dict(v1.data or {})

    response = await agreements_api.update_answers(
        agreement_id=agreement.id,
        data=agreements_api.UpdateAnswersRequest(
            answers={"term_years": 3}, check_compliance=False
        ),
        org_id=agreement.organization_id,
        db=db_session,
        current_user=test_user,
    )
    assert response.status == "updated"

    from app.services.agreement_versioning import list_versions

    versions = await list_versions(db_session, agreement.id)
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[-1].note == "Answers updated"
    assert (versions[-1].data or {})["term_years"] == 3

    # Version N was never mutated.
    await db_session.refresh(v1)
    assert (v1.data or {}) == v1_data_before


@pytest.mark.asyncio
async def test_update_answers_skips_identical_snapshot(
    db_session, versioned_agreement, test_user
):
    from app.api.v1 import agreements as agreements_api
    from app.services.agreement_versioning import list_versions

    agreement, _ = versioned_agreement
    payload = agreements_api.UpdateAnswersRequest(
        answers={"term_years": 5}, check_compliance=False
    )
    await agreements_api.update_answers(
        agreement_id=agreement.id,
        data=payload,
        org_id=agreement.organization_id,
        db=db_session,
        current_user=test_user,
    )
    await agreements_api.update_answers(
        agreement_id=agreement.id,
        data=payload,
        org_id=agreement.organization_id,
        db=db_session,
        current_user=test_user,
    )

    versions = await list_versions(db_session, agreement.id)
    # v1 + one append for the first save; the identical second save is skipped.
    assert [v.version_number for v in versions] == [1, 2]


@pytest.mark.asyncio
async def test_get_version_by_number(db_session, versioned_agreement):
    from app.services.agreement_versioning import get_version_by_number

    agreement, v1 = versioned_agreement
    found = await get_version_by_number(db_session, agreement.id, 1)
    assert found is not None and found.id == v1.id
    assert await get_version_by_number(db_session, agreement.id, 99) is None


@pytest.mark.asyncio
async def test_compare_versions_returns_content_and_data_diff(
    db_session, versioned_agreement, test_user
):
    from app.services.agreement_versioning import (
        compare_versions,
        create_version,
    )

    agreement, v1 = versioned_agreement
    v1.data = {"term_years": 1}
    await db_session.flush()
    await create_version(
        db=db_session,
        agreement=agreement,
        content="line one\nline two\n",
        created_by=test_user.id,
        data={"term_years": 2, "governing_law": "US"},
        note="Answers updated",
    )
    await db_session.commit()

    # Give v1 rendered content so the text diff is non-trivial.
    v1.content = "line one\n"
    await db_session.flush()
    await db_session.commit()

    result = await compare_versions(db_session, agreement.id, 1, 2)
    assert result["from_version"] == 1 and result["to_version"] == 2
    assert "+line two" in result["content_diff"]
    changed_keys = {c["key"] for c in result["data_diff"]["changed"]}
    assert "term_years" in changed_keys
    added_keys = {a["key"] for a in result["data_diff"]["added"]}
    assert "governing_law" in added_keys


@pytest.mark.asyncio
async def test_compare_versions_missing_version_raises(db_session, versioned_agreement):
    from app.services.agreement_versioning import compare_versions

    agreement, _ = versioned_agreement
    with pytest.raises(ValueError):
        await compare_versions(db_session, agreement.id, 1, 42)


@pytest.mark.asyncio
async def test_restore_version_appends_and_resets_working_data(
    db_session, versioned_agreement, test_user
):
    from app.services.agreement_versioning import (
        create_version,
        list_versions,
        restore_version,
    )

    agreement, v1 = versioned_agreement
    v1.data = {"term_years": 1, "governing_law": "US"}
    await db_session.flush()

    v2 = await create_version(
        db=db_session,
        agreement=agreement,
        content="",
        created_by=test_user.id,
        data={"term_years": 9, "governing_law": "SG"},
        note="Answers updated",
    )
    await db_session.commit()
    assert v2.version_number == 2

    restored = await restore_version(db_session, agreement, v1, test_user.id)
    await db_session.commit()

    assert restored.version_number == 3
    assert restored.note == "Restored from v1"
    assert restored.data == {"term_years": 1, "governing_law": "US"}
    assert agreement.data == {"term_years": 1, "governing_law": "US"}

    versions = await list_versions(db_session, agreement.id)
    assert [v.version_number for v in versions] == [1, 2, 3]


@pytest.mark.asyncio
async def test_create_version_endpoint_snapshots_current_state(
    db_session, versioned_agreement, test_user
):
    from app.api.v1 import agreements as agreements_api

    agreement, _ = versioned_agreement
    agreement.data = {"term_years": 4}
    await db_session.flush()

    version = await agreements_api.create_agreement_version(
        agreement_id=agreement.id,
        data=agreements_api.CreateVersionRequest(note="Baseline for legal"),
        org_id=agreement.organization_id,
        db=db_session,
        current_user=test_user,
    )
    assert version.version_number == 2
    assert version.note == "Baseline for legal"
    assert version.data == {"term_years": 4}


@pytest.mark.asyncio
async def test_restore_endpoint_missing_version_404(
    db_session, versioned_agreement, test_user
):
    from fastapi import HTTPException

    from app.api.v1 import agreements as agreements_api

    agreement, _ = versioned_agreement
    with pytest.raises(HTTPException) as exc:
        await agreements_api.restore_agreement_version(
            agreement_id=agreement.id,
            version_number=77,
            org_id=agreement.organization_id,
            db=db_session,
            current_user=test_user,
        )
    assert exc.value.status_code == 404
