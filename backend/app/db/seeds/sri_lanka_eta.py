import asyncio
import sys
import os
import uuid
from sqlalchemy import select

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from app.core.database import AsyncSessionLocal
from app.models.execution import ExecutionRequirement
from app.models.tenant import Organization
from app.models.agreement_type import AgreementType

async def seed_sri_lanka_eta():
    print("Seeding Sri Lanka ETA Execution Requirements...")
    async with AsyncSessionLocal() as db:
        # Get the default tenant
        tenant_query = await db.execute(select(Organization).limit(1))
        tenant = tenant_query.scalar_one_or_none()
        
        if not tenant:
            print("No tenant found. Please run seed_data.py first.")
            return
            
        # We look for a real estate / property agreement type or just apply it globally as a demonstration
        # In a real system, this would be tied to a specific "Commercial Lease" agreement type.
        ag_type_query = await db.execute(select(AgreementType).where(AgreementType.name == "Commercial Lease").limit(1))
        ag_type = ag_type_query.scalar_one_or_none()
        
        reqs = [
            ExecutionRequirement(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                agreement_type_id=ag_type.id if ag_type else None,
                requirement_type="notarization",
                description="Sri Lankan law (Electronic Transactions Act exception) imposes specific notarial requirements for transactions concerning land and immovable property. Electronic signatures are NOT sufficient.",
                severity="required",
                status="pending",
            ),
            ExecutionRequirement(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                agreement_type_id=ag_type.id if ag_type else None,
                requirement_type="witness",
                description="Transactions concerning land and immovable property in Sri Lanka require physical witnessing (minimum 2 witnesses alongside the notary).",
                severity="required",
                status="pending",
            )
        ]
        
        db.add_all(reqs)
        await db.commit()
        print("Sri Lanka ETA Execution Requirements seeded successfully.")

if __name__ == "__main__":
    asyncio.run(seed_sri_lanka_eta())
