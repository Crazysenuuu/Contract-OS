#!/usr/bin/env python3
"""Seed deterministic fixtures for the Playwright e2e suite.

Creates (idempotently):
    - login user  test@example.com / password123  (used by every e2e test)
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


async def seed_login_user() -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.security import hash_password
    from app.models.organization import Organization
    from app.models.rbac import OrganizationMember, Role
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == E2E_EMAIL))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                email=E2E_EMAIL,
                name="E2E Test User",
                password_hash=hash_password(E2E_PASSWORD),
                status="active",
            )
            db.add(user)
            await db.flush()
            print(f"  created user {E2E_EMAIL}")
        else:
            # Keep credentials deterministic across runs (e.g. after a
            # re-seed changed the hashing parameters).
            user.password_hash = hash_password(E2E_PASSWORD)
            if user.status != "active":
                user.status = "active"
            print(f"  user {E2E_EMAIL} already exists (credentials refreshed)")

        membership = (
            await db.execute(
                select(OrganizationMember.user_id).where(
                    OrganizationMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            org = Organization(
                name="E2E Test Org",
                slug="e2e-test-org",
                country="US",
                timezone="America/New_York",
            )
            db.add(org)
            await db.flush()
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
            print(f"  created org e2e-test-org for {E2E_EMAIL}")

        await db.commit()


async def main() -> int:
    print("Seeding e2e fixtures...")
    await seed_login_user()

    # Reference data the UI flows browse: agreement types, jurisdictions,
    # lifecycle states and transition rules.
    import seed_data

    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await seed_data.seed_agreement_types(db)
        await seed_data.seed_extended_agreement_types(db)
        await seed_data.seed_catalog_agreement_types(db)
        await seed_data.seed_lifecycle(db)
        await db.commit()
    print("✅ e2e seed complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
