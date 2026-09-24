#!/usr/bin/env python3
"""Dev utility: ensure a user has a working password + active org membership.

Create-or-update semantics, same patterns as scripts/seed_e2e.py:
  - create the user if missing (or refresh its password/status if present)
  - attach an active OrganizationMember row in the target org (reusing its
    `owner` role) if the user has no membership yet

Examples:
    python scripts/fix_user_org.py --email user@example.com --password password123
    DATABASE_URL=... python scripts/fix_user_org.py --email user@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_ORG_SLUG = "e2e-test-org"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default="password123")
    parser.add_argument("--org-slug", default=DEFAULT_ORG_SLUG)
    parser.add_argument("--name", default=None, help="Display name when creating the user")
    args = parser.parse_args()

    from sqlalchemy import func, select

    from app.core.config import get_settings_lazy
    from app.core.database import AsyncSessionLocal
    from app.core.security import hash_password, verify_password
    from app.models.organization import Organization
    from app.models.rbac import OrganizationMember, Role
    from app.models.user import User

    # Spec §72: fixture-style accounts must never be (re)written in production.
    if get_settings_lazy().environment == "production":
        print("❌ Refusing to run: ENVIRONMENT=production (spec §72)")
        return 2

    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.email == args.email))
        ).scalar_one_or_none()

        if user is None:
            user = User(
                email=args.email,
                name=args.name or args.email.split("@")[0].title(),
                password_hash=hash_password(args.password),
                status="active",
            )
            db.add(user)
            await db.flush()
            print(f"✅ created user {args.email}")
        else:
            user.password_hash = hash_password(args.password)
            user.status = "active"
            print(f"✅ refreshed credentials for {args.email}")

        # Active org membership (only if the user has none at all)
        count = await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.user_id == user.id)
        )
        if count:
            print(f"ℹ️  {args.email} already has {count} membership row(s); not touching them")
        else:
            org = (
                await db.execute(select(Organization).where(Organization.slug == args.org_slug))
            ).scalar_one_or_none()
            if org is None:
                org = Organization(
                    name=f"{args.email.split('@')[0].title()}'s Organization",
                    slug=args.org_slug,
                    country="US",
                    timezone="America/New_York",
                )
                db.add(org)
                await db.flush()
                print(f"✅ created org '{org.slug}'")
            role = (
                await db.execute(
                    select(Role).where(
                        Role.organization_id == org.id,
                        Role.name == "owner",
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
            print(f"✅ added active membership in '{org.slug}' (role: owner)")

        await db.commit()

    # Verify from a fresh session
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == args.email))).scalar_one()
        ok = verify_password(args.password, user.password_hash)
        memberships = (
            await db.execute(
                select(Organization.slug, OrganizationMember.status)
                .join(Organization, Organization.id == OrganizationMember.organization_id)
                .where(OrganizationMember.user_id == user.id)
            )
        ).all()
        print(f"password_ok={ok} status={user.status}")
        for slug, status in memberships:
            print(f"membership: org={slug} status={status}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
