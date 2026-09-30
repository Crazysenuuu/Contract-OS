"""Tests for the COPPA age gate on user registration."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.user import User


def _dob_for_age(age_years: int) -> str:
    today = date.today()
    try:
        return today.replace(year=today.year - age_years).isoformat()
    except ValueError:
        # Feb 29 birthdays
        return today.replace(year=today.year - age_years, day=28).isoformat()


@pytest.mark.asyncio
class TestCoppaAgeGate:
    async def test_under_13_rejected(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "kid@test.com",
                "name": "Kid User",
                "password": "KidPass123!",
                "date_of_birth": _dob_for_age(10),
            },
        )
        assert resp.status_code == 422

    async def test_exactly_13_accepted(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "teen@test.com",
                "name": "Teen User",
                "password": "TeenPass123!",
                "date_of_birth": _dob_for_age(13),
            },
        )
        assert resp.status_code in (200, 201)

    async def test_adult_accepted(self, client):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "adult@test.com",
                "name": "Adult User",
                "password": "AdultPass123!",
                "date_of_birth": _dob_for_age(30),
            },
        )
        assert resp.status_code in (200, 201)

    async def test_dob_never_persisted(self, client, db_session):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "nodob@test.com",
                "name": "No Dob",
                "password": "NoDOBPass123!",
                "date_of_birth": _dob_for_age(25),
            },
        )
        assert resp.status_code in (200, 201)
        row = (
            await db_session.execute(
                select(User).where(User.email == "nodob@test.com")
            )
        ).scalar_one()
        # Only the derived flag is stored; no DOB column exists.
        assert row.is_adult is True
        assert not hasattr(row, "date_of_birth")

    async def test_future_dob_rejected(self, client):
        future = (date.today() + timedelta(days=365)).isoformat()
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "future@test.com",
                "name": "Time Traveler",
                "password": "FuturePass123!",
                "date_of_birth": future,
            },
        )
        assert resp.status_code == 422
