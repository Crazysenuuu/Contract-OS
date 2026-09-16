#!/usr/bin/env python3
"""Create or promote a platform admin account.

Usage (from the frontend/ repo root or anywhere):
    backend/venv/bin/python backend/scripts/create_admin.py admin@example.com "Admin Name" [password]

Flags:
    --no-demo-org   Do not create a default organization/membership for the admin.

If <password> is omitted, a random one is generated and printed once.
If the email already exists, it is promoted to is_admin=True and its password is NOT
overwritten unless --reset-password is passed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Make the `app` package importable and resolve `.env` regardless of the shell cwd.
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

import uuid
from datetime import datetime, timezone

from sqlalchemy import select


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run(args: argparse.Namespace) -> int:
    from app.core.database import AsyncSessionLocal
    from app.core.security import hash_password
    from app.models.organization import Organization
    from app.models.rbac import Role, OrganizationMember
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == args.email))
        user = result.scalar_one_or_none()

        created = False
        if user is None:
            user = User(
                email=args.email,
                name=args.name,
                password_hash=hash_password(args.password),
                status="active",
                email_verified_at=_utcnow(),
                is_admin=True,
            )
            db.add(user)
            created = True
        else:
            user.is_admin = True
            if args.reset_password:
                user.password_hash = hash_password(args.password)
            if user.status == "pending_verification":
                user.status = "active"
                user.email_verified_at = _utcnow()

        await db.flush()

        if created and not args.no_demo_org:
            # Give the admin a home organization and an owner membership, mirroring
            # how regular signups are provisioned.
            import re

            slug = re.sub(r"[^a-z0-9]+", "-", args.name.lower()).strip("-") or "admin"
            org = Organization(
                name=f"{args.name}'s Organization",
                slug=f"{slug}-{uuid.uuid4().hex[:8]}",
                country="LK",
                timezone="Asia/Colombo",
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

        await db.commit()

        if created:
            print(f"✅ Created admin: {args.email} (name: {args.name})")
        else:
            print(f"✅ Existing user promoted to admin: {args.email}")
        print(f"   is_admin: {user.is_admin}")

        if created or args.reset_password:
            # Note: User instance refreshed/expired after commit; we already know the password.
            print(f"   password: {args.password}")

        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("name", nargs="?", default=None)
    parser.add_argument("password", nargs="?", default=None)
    parser.add_argument("--reset-password", action="store_true")
    parser.add_argument("--no-demo-org", action="store_true")
    args = parser.parse_args()

    if args.name is None:
        args.name = args.email.split("@")[0].replace(".", " ").title()
    if args.password is None:
        args.password = secrets.token_urlsafe(12)

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())