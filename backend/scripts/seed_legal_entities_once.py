#!/usr/bin/env python3
"""One-off: seed two legal entities for test@example.com's org (e2e DB).

The wizard specs (critical-flow, new-catalog-types) need the logged-in user's
org to own legal entities so the Party A / Party B selects have options.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

EMAIL = "test@example.com"
ENTITIES = [
    {"legal_name": "E2E Test Corp (Pvt) Ltd", "country": "US", "entity_type": "corporation"},
    {"legal_name": "E2E Counterparty LLC", "country": "US", "entity_type": "llc"},
]


async def main() -> int:
    from sqlalchemy import func, select

    from app.core.config import get_settings_lazy
    from app.core.database import AsyncSessionLocal
    from app.models.legal_entity import LegalEntity
    from app.models.rbac import OrganizationMember
    from app.models.user import User

    if get_settings_lazy().environment == "production":
        print("❌ Refusing to run: ENVIRONMENT=production (spec §72)")
        return 2

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one()
        org_id = await db.scalar(
            select(OrganizationMember.organization_id).where(
                OrganizationMember.user_id == user.id,
                OrganizationMember.status == "active",
            ).limit(1)
        )
        if org_id is None:
            print(f"❌ {EMAIL} has no active org membership")
            return 1

        existing = await db.scalar(
            select(func.count())
            .select_from(LegalEntity)
            .where(LegalEntity.organization_id == org_id)
        )
        if existing:
            print(f"ℹ️  org already has {existing} legal entity(ies); nothing to do")
            return 0

        for spec in ENTITIES:
            db.add(LegalEntity(organization_id=org_id, **spec))
        await db.commit()
        print(f"✅ created {len(ENTITIES)} legal entities for org {org_id}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
