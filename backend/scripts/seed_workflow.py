"""
Seed script for the default workflow definition.

Usage:
    cd backend
    source venv/bin/activate
    python -m scripts.seed_workflow
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import AsyncSessionLocal
from app.services.workflow_seed import seed_workflow


async def main():
    async with AsyncSessionLocal() as db:
        workflow = await seed_workflow(db, "mutual_nda_lk_v1")
        await db.commit()
        print(f"Workflow seeded: {workflow.key} (ID: {workflow.id})")


if __name__ == "__main__":
    asyncio.run(main())
