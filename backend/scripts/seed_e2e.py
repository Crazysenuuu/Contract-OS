#!/usr/bin/env python3
"""Seed deterministic fixtures for the Playwright e2e suite.

Creates (idempotently):
    - login user  test@example.com / password123  (used by every e2e test)
    - admin user  admin@example.com / password123 (for /admin pages)
    - agreement types / jurisdictions / lifecycle states (via seed_data)

Usage (backend must have a reachable DATABASE_URL):
    python scripts/seed_e2e.py

The e2e CI job runs this after `alembic upgrade head` and before starting
the backend, so the tests can log in without depending on runtime signup.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

E2E_EMAIL = "test@example.com"
E2E_PASSWORD = "password123"
E2E_ADMIN_EMAIL = "admin@example.com"
E2E_ADMIN_PASSWORD = "password123"
# Deterministic phone for SMS-channel verification (spec §44).
E2E_PHONE = "+15551230000"


async def seed_login_user() -> None:
    await _seed_user(E2E_EMAIL, E2E_PASSWORD, "E2E Test User", is_admin=False)


async def seed_admin_user() -> None:
    await _seed_user(E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD, "E2E Admin User", is_admin=True)


async def _seed_user(
    email: str, password: str, name: str, *, is_admin: bool
) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.security import hash_password
    from app.models.organization import Organization
    from app.models.rbac import OrganizationMember, Role
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                email=email,
                name=name,
                password_hash=hash_password(password),
                status="active",
                is_admin=is_admin,
                # Deterministic phone so SMS-channel e2e tests (spec §44)
                # can assert on gateway deliveries.
                phone=E2E_PHONE,
            )
            db.add(user)
            await db.flush()
            print(f"  created user {email} (is_admin={is_admin})")
        else:
            # Keep credentials deterministic across runs (e.g. after a
            # re-seed changed the hashing parameters).
            user.password_hash = hash_password(password)
            user.is_admin = is_admin
            if user.status != "active":
                user.status = "active"
            if not user.phone:
                user.phone = E2E_PHONE
            print(f"  user {email} already exists (credentials refreshed)")

        membership = (
            await db.execute(
                select(OrganizationMember.user_id).where(
                    OrganizationMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            # Reuse the shared e2e org if it exists (unique on slug), else
            # create it — otherwise a second seeded user crashes on the
            # organizations_slug unique constraint.
            org = (
                await db.execute(
                    select(Organization).where(Organization.slug == "e2e-test-org")
                )
            ).scalar_one_or_none()
            if org is None:
                org = Organization(
                    name="E2E Test Org",
                    slug="e2e-test-org",
                    country="US",
                    timezone="America/New_York",
                )
                db.add(org)
                await db.flush()
                print(f"  created org e2e-test-org")
            role = (
                await db.execute(
                    select(Role).where(
                        Role.organization_id == org.id, Role.name == "owner"
                    )
                )
            ).scalar_one_or_none()
            if role is None:
                role = Role(organization_id=org.id, name="owner")
                db.add(role)
                await db.flush()
            db.add(
                OrganizationMember(
                    organization_id=org.id,
                    user_id=user.id,
                    role_id=role.id,
                    status="active",
                )
            )
            print(f"  joined {email} to org e2e-test-org")

        await db.commit()


async def seed_legal_entities() -> None:
    """Seed two legal entities owned by the shared e2e org.

    The creation wizard's Party A / Party B selects list legal entities of
    the caller's org; without any, the wizard specs (critical-flow,
    new-catalog-types) cannot complete intake. Idempotent: skips when the
    org already owns entities.
    """
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionLocal
    from app.models.legal_entity import LegalEntity
    from app.models.organization import Organization

    async with AsyncSessionLocal() as db:
        org = (
            await db.execute(
                select(Organization).where(Organization.slug == "e2e-test-org")
            )
        ).scalar_one_or_none()
        if org is None:
            print("  ⚠️  org e2e-test-org missing; users must be seeded first")
            return
        count = await db.scalar(
            select(func.count())
            .select_from(LegalEntity)
            .where(LegalEntity.organization_id == org.id)
        )
        if count:
            print(f"  ⏭️  Legal entities already exist ({count})")
            return
        db.add_all(
            [
                LegalEntity(
                    organization_id=org.id,
                    legal_name="E2E Test Corp (Pvt) Ltd",
                    country="US",
                    entity_type="corporation",
                ),
                LegalEntity(
                    organization_id=org.id,
                    legal_name="E2E Counterparty LLC",
                    country="US",
                    entity_type="llc",
                ),
            ]
        )
        await db.commit()
        print("  created 2 legal entities in e2e-test-org")


async def main() -> int:
    # Spec §72: fixture users / demo data must never land in production.
    from app.core.config import get_settings_lazy

    if get_settings_lazy().environment == "production":
        print("❌ Refusing to seed e2e fixtures: ENVIRONMENT=production (spec §72)")
        return 2

    print("Seeding e2e fixtures...")
    await seed_login_user()
    await seed_admin_user()
    await seed_legal_entities()

    # Reference data the UI flows browse: agreement types, jurisdictions,
    # lifecycle states and transition rules.
    import seed_data

    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await seed_data.seed_agreement_types(db)
        await seed_data.seed_extended_agreement_types(db)
        await seed_data.seed_catalog_agreement_types(db)
        await seed_data.seed_lifecycle(db)
        # Jurisdictions feed the wizard's Governing-Law field (a required
        # intake control); without them the creation flow cannot render it.
        await seed_data.seed_jurisdictions(db)
        await db.commit()
    print("✅ e2e seed complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
