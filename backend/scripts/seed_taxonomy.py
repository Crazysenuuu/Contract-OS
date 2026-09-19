import asyncio
import os
import sys
import uuid
import re

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal
from app.models.agreement_type import AgreementType
from sqlalchemy import select

TAXONOMY = [
    # Corporate & Governance
    ("Shareholders Agreement", "Corporate"),
    ("Share Purchase Agreement", "Corporate"),
    ("Investment Agreement", "Corporate"),
    ("Convertible Note Agreement", "Corporate"),
    ("SAFE-style Investment Agreement", "Corporate"),
    ("Founder Agreement", "Corporate"),
    ("Board Resolution", "Corporate"),

    # Commercial
    ("Master Services Agreement", "Commercial"),
    ("Statement of Work", "Commercial"),
    ("Service Agreement", "Commercial"),
    ("Service Level Agreement", "Commercial"),
    ("Vendor Agreement", "Commercial"),
    ("Supplier Agreement", "Commercial"),
    ("Purchase Agreement", "Commercial"),
    ("Distribution Agreement", "Commercial"),
    ("Reseller Agreement", "Commercial"),
    ("Partnership Agreement", "Commercial"),
    ("Strategic Partnership Agreement", "Commercial"),
    ("Referral Agreement", "Commercial"),
    ("Commission Agreement", "Commercial"),

    # Confidentiality
    ("Non-Disclosure Agreement", "Confidentiality"),
    ("Mutual Non-Disclosure Agreement", "Confidentiality"),

    # Employment & HR
    ("Employment Agreement", "HR"),
    ("Independent Contractor Agreement", "HR"),
    ("Consultant Agreement", "HR"),
    ("Internship Agreement", "HR"),
    ("Offer Letter", "HR"),
    ("Employee IP Assignment Agreement", "HR"),
    ("Employee NDA", "HR"),
    ("Non-Solicitation Agreement", "HR"),

    # Technology
    ("Software Development Agreement", "Technology"),
    ("Software License Agreement", "Technology"),
    ("SaaS Agreement", "Technology"),
    ("API Agreement", "Technology"),
    ("Data Processing Agreement", "Technology"),
    ("IP Assignment Agreement", "Technology"),
    ("IP License Agreement", "Technology"),
    ("Trademark License Agreement", "Technology"),
    ("Technology Transfer Agreement", "Technology"),

    # Financial
    ("Loan Agreement", "Financial"),
    ("Promissory Note", "Financial"),
    ("Payment Agreement", "Financial"),
    ("Credit Agreement", "Financial"),
    ("Guarantee Agreement", "Financial"),
    ("Debt Settlement Agreement", "Financial"),

    # Real estate / physical
    ("Lease Agreement", "Real Estate"),
    ("Commercial Lease", "Real Estate"),
    ("Property Management Agreement", "Real Estate"),
    ("Facility Use Agreement", "Real Estate"),

    # Marketing
    ("Marketing Services Agreement", "Marketing"),
    ("Influencer Agreement", "Marketing"),
    ("Advertising Agreement", "Marketing"),
    ("Sponsorship Agreement", "Marketing"),
    ("Brand Ambassador Agreement", "Marketing"),
    ("Affiliate Agreement", "Marketing"),

    # Logistics & supply chain
    ("Transportation Agreement", "Logistics"),
    ("Logistics Agreement", "Logistics"),
    ("Warehousing Agreement", "Logistics"),
    ("Manufacturing Agreement", "Logistics"),
    ("Procurement Agreement", "Logistics"),
    ("Import/Export Agreement", "Logistics"),

    # Corporate transactions
    ("Merger Agreement", "Transactions"),
    ("Acquisition Agreement", "Transactions"),
    ("Asset Purchase Agreement", "Transactions"),
    ("Joint Venture Agreement", "Transactions"),
    ("Strategic Alliance Agreement", "Transactions"),

    # Dispute-related
    ("Settlement Agreement", "Disputes"),
    ("Mediation Agreement", "Disputes"),
    ("Arbitration Agreement", "Disputes"),
    ("Release / Waiver Agreement", "Disputes"),
]

def to_key(name: str) -> str:
    # converts "Shareholders Agreement" to "shareholders_agreement"
    key = name.lower()
    key = re.sub(r'[^a-z0-9\s-]', '', key)
    key = re.sub(r'[\s-]+', '_', key)
    return key

async def seed_taxonomy():
    print("Seeding Agreement Taxonomy...")
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    os.makedirs(templates_dir, exist_ok=True)
    
    async with AsyncSessionLocal() as db:
        # Get existing keys to avoid duplicates
        result = await db.execute(select(AgreementType.key))
        existing_keys = {row[0] for row in result.all()}
        
        added_count = 0
        for name, category in TAXONOMY:
            key = to_key(name)
            
            if key not in existing_keys:
                template_filename = f"{key}_lk_v1.jinja2"
                template_path = os.path.join(templates_dir, template_filename)
                
                # Default schema (dynamic questionnaire definition)
                default_schema = {
                    "sections": [
                        {
                            "id": "parties",
                            "title": "Parties",
                            "fields": [
                                {"key": "party_a", "type": "text", "label": "Party A Name", "required": True},
                                {"key": "party_b", "type": "text", "label": "Party B Name", "required": True},
                            ]
                        }
                    ]
                }
                
                new_type = AgreementType(
                    id=uuid.uuid4(),
                    key=key,
                    name=name,
                    description=f"Standard {name}",
                    category=category,
                    schema=default_schema,
                    template_key=template_filename.replace('.jinja2', ''),
                    status="active",
                    version=1
                )
                db.add(new_type)
                existing_keys.add(key)
                added_count += 1
                
                # Write a basic template stub if it doesn't exist
                if not os.path.exists(template_path):
                    with open(template_path, "w") as f:
                        f.write(f"# {name}\\n\\nThis agreement is made between {{{{ party_a }}}} and {{{{ party_b }}}}.")
                        
        if added_count > 0:
            await db.commit()
            print(f"Successfully seeded {added_count} new agreement types and templates.")
        else:
            print("Taxonomy already up to date. No new types added.")

if __name__ == "__main__":
    asyncio.run(seed_taxonomy())
