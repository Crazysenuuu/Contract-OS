"""Persistence tests for the DB-backed feature flag service.

The service used to keep flags in process memory: changes were lost on
restart and invisible to other workers. These tests pin the DB-backed
behavior — writes go through one session and are read back through a
*separate* session (like another worker would), proving the state lives
in the database, not in the service instance.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.feature_flag import FeatureFlagRecord
from app.services.feature_flags import (
    FeatureFlag,
    FeatureFlagService,
    FlagStatus,
    FlagType,
)


async def _fresh_session(engine) -> AsyncSession:
    """A second session on the same engine — simulates another worker."""
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory()


@pytest.mark.asyncio
async def test_created_flag_persists_across_sessions(engine):
    """A flag created through one session is visible to a fresh session."""
    service = FeatureFlagService()
    first = await _fresh_session(engine)
    second = await _fresh_session(engine)
    try:
        await service.create_flag(
            first,
            FeatureFlag(
                name="persist_me",
                description="survives restarts",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
                tags=["test"],
            ),
        )

        # New session = fresh process view: the flag must be there.
        flag = await service.get_flag(second, "persist_me")
        assert flag is not None
        assert flag.enabled is True
        assert flag.description == "survives restarts"
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_flag_update_persists_across_sessions(engine):
    """enable/disable and field updates are durable."""
    service = FeatureFlagService()
    writer = await _fresh_session(engine)
    reader = await _fresh_session(engine)
    try:
        await service.create_flag(
            writer,
            FeatureFlag(
                name="toggle_me",
                description="",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
            ),
        )
        await service.disable_flag(writer, "toggle_me")

        flag = await service.get_flag(reader, "toggle_me")
        assert flag.enabled is False
        assert flag.status == FlagStatus.INACTIVE
    finally:
        await writer.close()
        await reader.close()


@pytest.mark.asyncio
async def test_deleted_flag_is_gone_for_new_sessions(engine):
    """Deletion removes the row (and cascades overrides)."""
    service = FeatureFlagService()
    writer = await _fresh_session(engine)
    reader = await _fresh_session(engine)
    try:
        await service.create_flag(
            writer,
            FeatureFlag(
                name="delete_me",
                description="",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
            ),
        )
        await service.set_user_override(writer, "delete_me", "user-1", True)

        assert await service.delete_flag(writer, "delete_me") is True

        assert await service.get_flag(reader, "delete_me") is None
        assert await service.get_user_overrides(reader, "delete_me") == {}
    finally:
        await writer.close()
        await reader.close()


@pytest.mark.asyncio
async def test_user_override_persists_and_affects_evaluation(engine):
    """Overrides are stored in the DB and honored by evaluation."""
    service = FeatureFlagService()
    writer = await _fresh_session(engine)
    evaluator = await _fresh_session(engine)
    try:
        await service.create_flag(
            writer,
            FeatureFlag(
                name="override_me",
                description="",
                flag_type=FlagType.BOOLEAN,
                enabled=False,
            ),
        )
        await service.set_user_override(writer, "override_me", "user-1", True)
        await service.set_user_override(writer, "override_me", "user-2", False)

        result = await service.evaluate(evaluator, "override_me", user_id="user-1")
        assert result.enabled is True
        assert result.reason == "override"

        result = await service.evaluate(evaluator, "override_me", user_id="user-2")
        assert result.enabled is False
        assert result.reason == "override"

        # Unknown user falls through to the flag's own state.
        result = await service.evaluate(evaluator, "override_me", user_id="user-3")
        assert result.enabled is False
        assert result.reason == "boolean_flag"

        # Clearing removes the row durably.
        await service.clear_user_override(writer, "override_me", "user-1")
        result = await service.evaluate(evaluator, "override_me", user_id="user-1")
        assert result.reason == "boolean_flag"
    finally:
        await writer.close()
        await evaluator.close()


@pytest.mark.asyncio
async def test_default_flags_seeded_once_and_idempotent(engine):
    """Default flags are seeded into the DB and seeding is idempotent."""
    service = FeatureFlagService()
    session = await _fresh_session(engine)
    try:
        flags = await service.list_flags(session)
        names = {f.name for f in flags}
        assert "ai_analysis" in names
        assert "esignature_integration" in names
        assert len(names) >= 8

        # Second seeding must not duplicate or raise.
        await service.ensure_default_flags(session)
        again = await service.list_flags(session)
        assert len([f for f in again if f.name == "ai_analysis"]) == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_evaluate_all_flags_reads_persisted_state(engine):
    """Bulk evaluation reflects DB state, including overrides."""
    service = FeatureFlagService()
    writer = await _fresh_session(engine)
    evaluator = await _fresh_session(engine)
    try:
        await service.create_flag(
            writer,
            FeatureFlag(
                name="bulk_on",
                description="",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
            ),
        )
        await service.set_user_override(writer, "bulk_on", "user-9", False)

        results = await service.evaluate_all_flags(evaluator, user_id="user-9")
        assert results["bulk_on"] is False
        assert results["ai_analysis"] is True
    finally:
        await writer.close()
        await evaluator.close()


@pytest.mark.asyncio
async def test_import_upserts_existing_flags(engine):
    """Import updates existing rows instead of failing on duplicates."""
    service = FeatureFlagService()
    session = await _fresh_session(engine)
    try:
        imported = await service.import_flags(
            session,
            [
                {
                    "name": "ai_analysis",
                    "description": "updated description",
                    "flag_type": "boolean",
                    "enabled": False,
                    "tags": ["imported"],
                },
                {
                    "name": "brand_new_flag",
                    "description": "created by import",
                    "flag_type": "boolean",
                    "enabled": True,
                },
            ],
        )
        assert imported == 2

        flag = await service.get_flag(session, "ai_analysis")
        assert flag.description == "updated description"
        assert flag.enabled is False

        new_flag = await service.get_flag(session, "brand_new_flag")
        assert new_flag is not None
        assert new_flag.enabled is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_export_round_trips_through_import(engine):
    """Export → import preserves flag state (backup/restore path)."""
    service = FeatureFlagService()
    session = await _fresh_session(engine)
    try:
        await service.create_flag(
            session,
            FeatureFlag(
                name="roundtrip",
                description="rt",
                flag_type=FlagType.PERCENTAGE,
                enabled=True,
                percentage=37.5,
                tags=["rt"],
            ),
        )
        exported = await service.export_flags(session)
        by_name = {f["name"]: f for f in exported}
        assert by_name["roundtrip"]["percentage"] == 37.5

        # Mutate then restore from the export.
        await service.update_flag(session, "roundtrip", {"percentage": 5.0})
        await service.import_flags(session, [by_name["roundtrip"]])
        flag = await service.get_flag(session, "roundtrip")
        assert flag.percentage == 37.5
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_api_create_then_worker_restart_sees_flag(client, auth_headers, engine):
    """API-level: a flag created via HTTP is durable across 'restarts'."""
    response = await client.post(
        "/api/v1/feature-flags/flags",
        json={
            "name": "api_persisted",
            "description": "created over HTTP",
            "flag_type": "boolean",
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200

    # A brand-new session simulates a restarted worker.
    service = FeatureFlagService()
    fresh = await _fresh_session(engine)
    try:
        flag = await service.get_flag(fresh, "api_persisted")
        assert flag is not None
        assert flag.enabled is True
    finally:
        await fresh.close()


@pytest.mark.asyncio
async def test_flag_rows_use_expected_columns(db_session: AsyncSession):
    """The table stores the full flag shape (env overrides, kill switch)."""
    record = FeatureFlagRecord(
        name="shape_check",
        description="d",
        flag_type=FlagType.KILL_SWITCH.value,
        status=FlagStatus.ACTIVE.value,
        enabled=True,
        is_kill_switch=True,
        environments={"production": {"enabled": False}},
        tags=["infra"],
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)

    assert record.is_kill_switch is True
    assert record.environments == {"production": {"enabled": False}}
    assert record.tags == ["infra"]
