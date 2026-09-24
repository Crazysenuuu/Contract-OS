"""
Seed data script for ContractOS.

Populates the database with:
- Jurisdictions and jurisdiction-specific clause templates
- Agreement types with JSON schemas
- Sample organization and user (for dev)
- Company policies (sample compliance rules)
- Languages for i18n

Usage:
    cd backend
    source venv/bin/activate
    python seed_data.py
"""
import asyncio
import sys
import os
import uuid
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import AsyncSessionLocal
from app.models.base import Base
from app.models import *  # noqa: Import all models


# ============================================================
# JURISDICTIONS
# ============================================================

JURISDICTIONS = [
    {
        "id": str(uuid.UUID("c0000000-0000-0000-0000-000000000001")),
        "code": "LK",
        "name": "Sri Lanka",
        "region": "South Asia",
        "language": "si",
        "legal_system": "Mixed",
        "currency": "LKR",
        "timezone": "Asia/Colombo",
        "required_clauses": ["governing_law", "dispute_resolution", "limitation_of_liability"],
        "prohibited_clauses": ["unlimited_liability"],
        "signature_requirements": {
            "min_witnesses": 2,
            "requires_notarization": False,
            "requires_stamp_duty": True,
            "stamp_duty_rate": 0.01,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "arbitration",
        "arbitration_institution": "SLIARC",
        "default_confidentiality_period_years": 2,
        "statute_of_limitations_years": 6,
        "is_active": True,
    },
    {
        "id": str(uuid.UUID("c0000000-0000-0000-0000-000000000002")),
        "code": "SG",
        "name": "Singapore",
        "region": "Southeast Asia",
        "language": "en",
        "legal_system": "Common Law",
        "currency": "SGD",
        "timezone": "Asia/Singapore",
        "required_clauses": ["governing_law", "dispute_resolution"],
        "prohibited_clauses": [],
        "signature_requirements": {
            "min_witnesses": 1,
            "requires_notarization": False,
            "requires_stamp_duty": False,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "arbitration",
        "arbitration_institution": "SIAC",
        "default_confidentiality_period_years": 2,
        "statute_of_limitations_years": 6,
        "is_active": True,
    },
    {
        "id": str(uuid.UUID("c0000000-0000-0000-0000-000000000003")),
        "code": "US",
        "name": "United States",
        "region": "North America",
        "language": "en",
        "legal_system": "Common Law",
        "currency": "USD",
        "timezone": "America/New_York",
        "required_clauses": ["governing_law"],
        "prohibited_clauses": [],
        "signature_requirements": {
            "min_witnesses": 0,
            "requires_notarization": False,
            "requires_stamp_duty": False,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "litigation",
        "arbitration_institution": None,
        "default_confidentiality_period_years": 3,
        "statute_of_limitations_years": 4,
        "is_active": True,
    },
    {
        "id": str(uuid.UUID("c0000000-0000-0000-0000-000000000004")),
        "code": "GB",
        "name": "United Kingdom",
        "region": "Europe",
        "language": "en",
        "legal_system": "Common Law",
        "currency": "GBP",
        "timezone": "Europe/London",
        "required_clauses": ["governing_law", "dispute_resolution"],
        "prohibited_clauses": [],
        "signature_requirements": {
            "min_witnesses": 1,
            "requires_notarization": False,
            "requires_stamp_duty": False,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "litigation",
        "arbitration_institution": None,
        "default_confidentiality_period_years": 3,
        "statute_of_limitations_years": 6,
        "is_active": True,
    },
    {
        "id": str(uuid.UUID("c0000000-0000-0000-0000-000000000005")),
        "code": "IN",
        "name": "India",
        "region": "South Asia",
        "language": "hi",
        "legal_system": "Mixed",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "required_clauses": ["governing_law", "dispute_resolution", "limitation_of_liability"],
        "prohibited_clauses": [],
        "signature_requirements": {
            "min_witnesses": 2,
            "requires_notarization": False,
            "requires_stamp_duty": True,
            "stamp_duty_rate": 0.03,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "arbitration",
        "arbitration_institution": "Mumbai Centre for International Arbitration",
        "default_confidentiality_period_years": 2,
        "statute_of_limitations_years": 3,
        "is_active": True,
    },
    {
        "id": str(uuid.UUID("c0000000-0000-0000-0000-000000000006")),
        "code": "DE",
        "name": "Germany",
        "region": "Europe",
        "language": "de",
        "legal_system": "Civil Law",
        "currency": "EUR",
        "timezone": "Europe/Berlin",
        "required_clauses": ["governing_law", "dispute_resolution"],
        "prohibited_clauses": ["unfair_contract_terms"],
        "signature_requirements": {
            "min_witnesses": 0,
            "requires_notarization": False,
            "requires_stamp_duty": False,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "litigation",
        "arbitration_institution": None,
        "default_confidentiality_period_years": 2,
        "statute_of_limitations_years": 3,
        "is_active": True,
    },
]


# ============================================================
# JURISDICTION CLAUSES
# ============================================================

JURISDICTION_CLAUSES = [
    # Sri Lanka clauses
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000001")),
        "clause_type": "governing_law",
        "name": "Governing Law (Sri Lanka)",
        "description": "Standard governing law clause for Sri Lanka contracts",
        "standard_text": "This Agreement shall be governed by and construed in accordance with the laws of the Democratic Socialist Republic of Sri Lanka.",
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000001")),
        "clause_type": "dispute_resolution",
        "name": "Dispute Resolution - SLIARC (Sri Lanka)",
        "description": "Standard arbitration clause using SLIARC",
        "standard_text": "Any dispute arising out of or in connection with this Agreement shall be referred to and finally resolved by arbitration administered by the Sri Lanka Institute of Arbitrators (SLIARC) in accordance with the SLIARC Arbitration Rules for the time being in force.",
        "alternative_texts": [
            {
                "text": "Any dispute arising out of or in connection with this Agreement shall be submitted to the courts of Sri Lanka.",
                "conditions": "When parties prefer litigation over arbitration",
                "risk_level": "medium",
            }
        ],
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000001")),
        "clause_type": "confidentiality",
        "name": "Confidentiality (Sri Lanka)",
        "description": "Standard confidentiality clause for Sri Lanka",
        "standard_text": "Each party agrees to maintain the confidentiality of all Confidential Information disclosed by the other party and shall not disclose such information to any third party without the prior written consent of the disclosing party.",
        "risk_level": "low",
        "is_mandatory": False,
        "applies_to_types": ["mutual_nda", "service_agreement", "employment"],
    },
    # Singapore clauses
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000002")),
        "clause_type": "governing_law",
        "name": "Governing Law (Singapore)",
        "description": "Standard governing law clause for Singapore contracts",
        "standard_text": "This Agreement shall be governed by and construed in accordance with the laws of the Republic of Singapore.",
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000002")),
        "clause_type": "dispute_resolution",
        "name": "Dispute Resolution - SIAC (Singapore)",
        "description": "Standard arbitration clause using SIAC",
        "standard_text": "Any dispute arising out of or in connection with this Agreement, including any question regarding its existence, validity or termination, shall be referred to and finally resolved by arbitration administered by the Singapore International Arbitration Centre (SIAC) in accordance with the Arbitration Rules of SIAC for the time being in force.",
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
    # US clauses
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000003")),
        "clause_type": "governing_law",
        "name": "Governing Law (US - Delaware)",
        "description": "Standard governing law clause for US contracts (Delaware)",
        "standard_text": "This Agreement shall be governed by and construed in accordance with the laws of the State of Delaware, without regard to its conflict of laws principles.",
        "alternative_texts": [
            {
                "text": "This Agreement shall be governed by and construed in accordance with the laws of the State of New York, without regard to its conflict of laws principles.",
                "conditions": "New York governing law",
                "risk_level": "low",
            },
            {
                "text": "This Agreement shall be governed by and construed in accordance with the laws of the State of California, without regard to its conflict of laws principles.",
                "conditions": "California governing law",
                "risk_level": "low",
            },
        ],
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000003")),
        "clause_type": "limitation_of_liability",
        "name": "Limitation of Liability (US)",
        "description": "Standard limitation of liability clause for US contracts",
        "standard_text": "IN NO EVENT SHALL EITHER PARTY BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS OR REVENUES, WHETHER INCURRED DIRECTLY OR INDIRECTLY, OR ANY LOSS OF DATA, USE, GOOD-WILL, OR OTHER INTANGIBLE LOSSES. IN NO EVENT SHALL EITHER PARTY'S AGGREGATE LIABILITY EXCEED THE AMOUNTS PAID OR PAYABLE BY THE RECEIVING PARTY TO THE DISCLOSING PARTY UNDER THIS AGREEMENT DURING THE TWELVE (12) MONTHS PRECEDING THE EVENT GIVING RISE TO THE CLAIM.",
        "risk_level": "low",
        "is_mandatory": False,
        "applies_to_types": ["service_agreement", "sow", "msa"],
    },
    # UK clauses
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000004")),
        "clause_type": "governing_law",
        "name": "Governing Law (England)",
        "description": "Standard governing law clause for England",
        "standard_text": "This Agreement and any dispute or claim (including non-contractual disputes or claims) arising out of or in connection with it or its subject matter or formation shall be governed by and construed in accordance with the law of England and Wales.",
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
    {
        "jurisdiction_id": str(uuid.UUID("c0000000-0000-0000-0000-000000000004")),
        "clause_type": "dispute_resolution",
        "name": "Dispute Resolution (England)",
        "description": "Standard dispute resolution clause for England",
        "standard_text": "The courts of England and Wales shall have exclusive jurisdiction to settle any dispute or claim (including non-contractual disputes or claims) arising out of or in connection with this Agreement.",
        "alternative_texts": [
            {
                "text": "Any dispute arising out of or in connection with this Agreement shall be referred to and finally resolved by arbitration under the London Court of International Arbitration (LCIA) Rules.",
                "conditions": "When parties prefer LCIA arbitration",
                "risk_level": "low",
            }
        ],
        "risk_level": "low",
        "is_mandatory": True,
        "applies_to_types": ["all"],
    },
]


# ============================================================
# AGREEMENT TYPES
# ============================================================

AGREEMENT_TYPES = [
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000001")),
        "key": "mutual_nda",
        "template_key": "mutual_nda_lk_v1",
        "name": "Mutual Non-Disclosure Agreement",
        "description": "Two-way confidentiality agreement for sharing sensitive information",
        "category": "confidentiality",
        "status": "active",
        "version": 1,
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "disclosing_party_name", "type": "text", "label": "Disclosing Party Name", "required": True},
                {"key": "disclosing_party_address", "type": "text", "label": "Disclosing Party Address", "required": True},
                {"key": "receiving_party_name", "type": "text", "label": "Receiving Party Name", "required": True},
                {"key": "receiving_party_address", "type": "text", "label": "Receiving Party Address", "required": True},
                {"key": "purpose", "type": "textarea", "label": "Purpose of Disclosure", "required": True},
                {"key": "confidentiality_period", "type": "select", "label": "Confidentiality Period", "options": ["1 year", "2 years", "3 years", "5 years", "Indefinite"], "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
                {"key": "dispute_resolution", "type": "select", "label": "Dispute Resolution", "options": ["Arbitration", "Mediation", "Litigation"], "required": True},
            ],
            "clauses": [
                "confidentiality",
                "term_and_duration",
                "permitted_disclosure",
                "return_of_materials",
                "intellectual_property",
                "remedies",
                "governing_law",
                "dispute_resolution",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000002")),
        "key": "unilateral_nda",
        "template_key": "unilateral_nda_lk_v1",
        "name": "Unilateral Non-Disclosure Agreement",
        "description": "One-way confidentiality agreement where only one party discloses",
        "category": "confidentiality",
        "status": "active",
        "version": 1,
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "disclosing_party_name", "type": "text", "label": "Disclosing Party Name", "required": True},
                {"key": "disclosing_party_address", "type": "text", "label": "Disclosing Party Address", "required": True},
                {"key": "receiving_party_name", "type": "text", "label": "Receiving Party Name", "required": True},
                {"key": "receiving_party_address", "type": "text", "label": "Receiving Party Address", "required": True},
                {"key": "purpose", "type": "textarea", "label": "Purpose of Disclosure", "required": True},
                {"key": "confidentiality_period", "type": "select", "label": "Confidentiality Period", "options": ["1 year", "2 years", "3 years", "5 years", "Indefinite"], "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "confidentiality",
                "term_and_duration",
                "permitted_disclosure",
                "return_of_materials",
                "remedies",
                "governing_law",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000003")),
        "key": "service_agreement",
        "template_key": "service_agreement_lk_v1",
        "name": "Service Agreement",
        "description": "Standard service agreement for professional services",
        "category": "services",
        "status": "active",
        "version": 1,
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "provider_name", "type": "text", "label": "Service Provider Name", "required": True},
                {"key": "client_name", "type": "text", "label": "Client Name", "required": True},
                {"key": "service_description", "type": "textarea", "label": "Service Description", "required": True},
                {"key": "service_fee", "type": "number", "label": "Service Fee", "required": True},
                {"key": "payment_terms", "type": "select", "label": "Payment Terms", "options": ["Net 30", "Net 60", "Net 90", "Upon Receipt"], "required": True},
                {"key": "term_length", "type": "select", "label": "Term Length", "options": ["6 months", "1 year", "2 years", "3 years"], "required": True},
                {"key": "auto_renew", "type": "boolean", "label": "Auto-Renew", "required": False},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "scope_of_services",
                "payment_terms",
                "term_and_termination",
                "confidentiality",
                "intellectual_property",
                "warranties",
                "limitation_of_liability",
                "indemnification",
                "governing_law",
                "dispute_resolution",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000004")),
        "key": "employment",
        "template_key": "employment_lk_v1",
        "name": "Employment Agreement",
        "description": "Standard employment contract",
        "category": "employment",
        "status": "active",
        "version": 1,
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Start Date", "required": True},
                {"key": "employer_name", "type": "text", "label": "Employer Name", "required": True},
                {"key": "employee_name", "type": "text", "label": "Employee Name", "required": True},
                {"key": "job_title", "type": "text", "label": "Job Title", "required": True},
                {"key": "salary", "type": "number", "label": "Annual Salary", "required": True},
                {"key": "currency", "type": "select", "label": "Currency", "options": ["USD", "LKR", "SGD", "GBP", "EUR"], "required": True},
                {"key": "probation_period", "type": "select", "label": "Probation Period", "options": ["None", "3 months", "6 months"], "required": True},
                {"key": "notice_period", "type": "select", "label": "Notice Period", "options": ["1 week", "2 weeks", "1 month", "2 months", "3 months"], "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "position_and_duties",
                "compensation",
                "benefits",
                "probation",
                "confidentiality",
                "non_compete",
                "non_solicitation",
                "intellectual_property",
                "termination",
                "governing_law",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000005")),
        "key": "master_services_agreement",
        "name": "Master Services Agreement",
        "description": "Umbrella agreement governing the overall commercial relationship, under which scope-specific Statements of Work are issued",
        "category": "services",
        "status": "active",
        "version": 1,
        "template_key": "msa_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "provider_name", "type": "text", "label": "Service Provider Name", "required": True},
                {"key": "provider_address", "type": "text", "label": "Service Provider Address", "required": True},
                {"key": "client_name", "type": "text", "label": "Client Name", "required": True},
                {"key": "client_address", "type": "text", "label": "Client Address", "required": True},
                {"key": "term_months", "type": "number", "label": "Initial Term (months)", "required": True},
                {"key": "auto_renew", "type": "boolean", "label": "Auto-Renew", "required": False},
                {"key": "notice_period_days", "type": "number", "label": "Non-Renewal Notice Period (days)", "required": True},
                {"key": "billing_cycle", "type": "select", "label": "Billing Cycle", "options": ["Monthly", "Quarterly", "Annually", "Project-based"], "required": True},
                {"key": "payment_terms", "type": "select", "label": "Payment Terms", "options": ["Net 15", "Net 30", "Net 45", "Net 60"], "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
                {"key": "dispute_resolution", "type": "select", "label": "Dispute Resolution", "options": ["Arbitration", "Mediation", "Litigation"], "required": True},
            ],
            "clauses": [
                "engagement_of_services",
                "statements_of_work",
                "responsibilities",
                "fees_and_payment",
                "taxes",
                "invoicing",
                "expenses",
                "intellectual_property",
                "warranties",
                "limitation_of_liability",
                "indemnification",
                "confidentiality",
                "data_protection",
                "compliance",
                "insurance",
                "term_and_renewal",
                "termination",
                "force_majeure",
                "governing_law",
                "dispute_resolution",
                "assignment",
                "notices",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000006")),
        "key": "statement_of_work",
        "name": "Statement of Work",
        "description": "Scope-specific agreement issued under a Master Services Agreement",
        "category": "services",
        "status": "active",
        "version": 1,
        "template_key": "sow_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "provider_name", "type": "text", "label": "Service Provider Name", "required": True},
                {"key": "client_name", "type": "text", "label": "Client Name", "required": True},
                {"key": "sow_title", "type": "text", "label": "Statement of Work Title", "required": True},
                {"key": "sow_description", "type": "textarea", "label": "Scope Description", "required": True},
                {"key": "deliverables", "type": "textarea", "label": "Deliverables", "required": True},
                {"key": "milestones", "type": "textarea", "label": "Milestones & Timeline", "required": True},
                {"key": "sow_fee", "type": "number", "label": "SOW Fee", "required": True},
                {"key": "payment_schedule", "type": "select", "label": "Payment Schedule", "options": ["Milestone-based", "Monthly", "Upon Completion"], "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "scope_of_work",
                "deliverables",
                "milestones",
                "fees_and_payment",
                "changes",
                "acceptance",
                "warranties",
                "governing_law",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000007")),
        "key": "software_development",
        "name": "Software Development Agreement",
        "description": "Custom software development engagement covering ownership, delivery and support",
        "category": "software",
        "status": "active",
        "version": 1,
        "template_key": "software_dev_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "developer_name", "type": "text", "label": "Developer Name", "required": True},
                {"key": "client_name", "type": "text", "label": "Client Name", "required": True},
                {"key": "project_description", "type": "textarea", "label": "Project Description", "required": True},
                {"key": "deliverables", "type": "textarea", "label": "Deliverables", "required": True},
                {"key": "development_fee", "type": "number", "label": "Development Fee", "required": True},
                {"key": "payment_terms", "type": "select", "label": "Payment Terms", "options": ["Net 30", "Net 60", "Milestone-based"], "required": True},
                {"key": "ip_ownership", "type": "select", "label": "IP Ownership", "options": ["Client owns all", "Developer owns all", "Shared"], "required": True},
                {"key": "source_code_escrow", "type": "boolean", "label": "Source Code Escrow", "required": False},
                {"key": "support_period_months", "type": "number", "label": "Post-Delivery Support (months)", "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
                # Dynamic questionnaire (spec §4.2 / §15): personal-data branch
                # reveals the DPA fieldset and, at render time, Section 10A.
                {"key": "personal_data_processed", "type": "select", "label": "Will personal data be processed under this Agreement?", "options": ["Yes", "No"], "required": False},
                {"key": "processing_purpose", "type": "textarea", "label": "Purpose of Processing", "required": False, "condition": {"field": "personal_data_processed", "operator": "equals", "value": "Yes"}},
                {"key": "data_categories", "type": "textarea", "label": "Categories of Personal Data", "required": False, "condition": {"field": "personal_data_processed", "operator": "equals", "value": "Yes"}},
                {"key": "data_subjects", "type": "textarea", "label": "Categories of Data Subjects", "required": False, "condition": {"field": "personal_data_processed", "operator": "equals", "value": "Yes"}},
                {"key": "cross_border_transfers", "type": "select", "label": "Will personal data be transferred outside the jurisdiction?", "options": ["Yes", "No"], "required": False, "condition": {"field": "personal_data_processed", "operator": "equals", "value": "Yes"}},
                {"key": "data_retention_months", "type": "number", "label": "Data Retention Period (months)", "required": False, "condition": {"field": "personal_data_processed", "operator": "equals", "value": "Yes"}},
            ],
            "clauses": [
                "project_scope",
                "deliverables",
                "development_process",
                "acceptance",
                "intellectual_property",
                "source_code_escrow",
                "warranties",
                "support",
                "fees_and_payment",
                "confidentiality",
                "limitation_of_liability",
                "termination",
                "governing_law",
                "dispute_resolution",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000008")),
        "key": "saas_subscription",
        "name": "SaaS Subscription Agreement",
        "description": "Software-as-a-Service subscription terms for cloud-based services",
        "category": "software",
        "status": "active",
        "version": 1,
        "template_key": "saas_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "provider_name", "type": "text", "label": "Service Provider Name", "required": True},
                {"key": "client_name", "type": "text", "label": "Customer Name", "required": True},
                {"key": "subscription_plan", "type": "text", "label": "Subscription Plan / Tier", "required": True},
                {"key": "seats_number", "type": "number", "label": "Number of Seats / Users", "required": True},
                {"key": "subscription_fee", "type": "number", "label": "Subscription Fee", "required": True},
                {"key": "billing_frequency", "type": "select", "label": "Billing Frequency", "options": ["Monthly", "Quarterly", "Annually"], "required": True},
                {"key": "term_months", "type": "number", "label": "Term (months)", "required": True},
                {"key": "auto_renew", "type": "boolean", "label": "Auto-Renew", "required": False},
                {"key": "uptime_sla", "type": "number", "label": "Uptime SLA (%)", "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "subscription_services",
                "service_level",
                "support",
                "fees_and_payment",
                "taxes",
                "restrictions",
                "customer_data",
                "privacy",
                "security",
                "intellectual_property",
                "confidentiality",
                "warranties",
                "disclaimers",
                "limitation_of_liability",
                "indemnification",
                "term_and_renewal",
                "suspension_and_termination",
                "governing_law",
                "dispute_resolution",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-000000000009")),
        "key": "independent_contractor",
        "name": "Independent Contractor Agreement",
        "description": "Engagement of an independent contractor (not an employee)",
        "category": "employment",
        "status": "active",
        "version": 1,
        "template_key": "contractor_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "contractor_name", "type": "text", "label": "Contractor Name", "required": True},
                {"key": "company_name", "type": "text", "label": "Engaging Company", "required": True},
                {"key": "services_description", "type": "textarea", "label": "Services to be Provided", "required": True},
                {"key": "compensation", "type": "number", "label": "Compensation", "required": True},
                {"key": "compensation_basis", "type": "select", "label": "Compensation Basis", "options": ["Hourly", "Fixed Fee", "Per Deliverable", "Monthly"], "required": True},
                {"key": "term_months", "type": "number", "label": "Term (months)", "required": True},
                {"key": "independent_status", "type": "boolean", "label": "Explicitly Independent (not an employee)", "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "services",
                "compensation",
                "independent_contractor_status",
                "taxes_and_benefits",
                "intellectual_property",
                "confidentiality",
                "non_solicitation",
                "non_compete",
                "conformity",
                "term_and_termination",
                "governing_law",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-00000000000a")),
        "key": "consultancy",
        "name": "Consultancy Agreement",
        "description": "Professional consulting engagement with defined outcomes and deliverables",
        "category": "services",
        "status": "active",
        "version": 1,
        "template_key": "consultancy_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "consultant_name", "type": "text", "label": "Consultant Name", "required": True},
                {"key": "client_name", "type": "text", "label": "Client Name", "required": True},
                {"key": "services_description", "type": "textarea", "label": "Consulting Services", "required": True},
                {"key": "deliverables", "type": "textarea", "label": "Deliverables", "required": True},
                {"key": "consulting_fee", "type": "number", "label": "Consulting Fee", "required": True},
                {"key": "payment_terms", "type": "select", "label": "Payment Terms", "options": ["Net 30", "Net 60", "Retainer"], "required": True},
                {"key": "term_months", "type": "number", "label": "Term (months)", "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "services",
                "deliverables",
                "scheduling",
                "fees_and_expenses",
                "intellectual_property",
                "confidentiality",
                "independent_contractor_status",
                "warranties",
                "term_and_termination",
                "governing_law",
                "dispute_resolution",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-00000000000b")),
        "key": "vendor_supplier",
        "name": "Vendor / Supplier Agreement",
        "description": "Procurement agreement governing the supply of goods or services",
        "category": "procurement",
        "status": "active",
        "version": 1,
        "template_key": "vendor_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "supplier_name", "type": "text", "label": "Supplier Name", "required": True},
                {"key": "buyer_name", "type": "text", "label": "Buyer Name", "required": True},
                {"key": "goods_services", "type": "textarea", "label": "Goods/Services Supplied", "required": True},
                {"key": "pricing", "type": "number", "label": "Price", "required": True},
                {"key": "payment_terms", "type": "select", "label": "Payment Terms", "options": ["Net 30", "Net 60", "Letter of Credit", "Cash on Delivery"], "required": True},
                {"key": "delivery_terms", "type": "text", "label": "Delivery Terms (Incoterms if applicable)", "required": True},
                {"key": "term_months", "type": "number", "label": "Term (months)", "required": True},
                {"key": "auto_renew", "type": "boolean", "label": "Auto-Renew", "required": False},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "supply_of_goods",
                "delivery",
                "quality_and_warranties",
                "pricing",
                "payment_terms",
                "inspection",
                "intellectual_property",
                "confidentiality",
                "compliance",
                "limitation_of_liability",
                "indemnification",
                "term_and_renewal",
                "termination",
                "governing_law",
                "dispute_resolution",
                "entire_agreement",
            ],
        },
    },
    {
        "id": str(uuid.UUID("b0000000-0000-0000-0000-00000000000c")),
        "key": "partnership",
        "name": "Partnership Agreement",
        "description": "General partnership governing two or more partners sharing profits and control",
        "category": "entity",
        "status": "active",
        "version": 1,
        "template_key": "partnership_lk_v1",
        "schema": {
            "questions": [
                {"key": "effective_date", "type": "date", "label": "Effective Date", "required": True},
                {"key": "partnership_name", "type": "text", "label": "Partnership Name", "required": True},
                {"key": "partners", "type": "textarea", "label": "Partner Names", "required": True},
                {"key": "purpose", "type": "textarea", "label": "Purpose of Partnership", "required": True},
                {"key": "capital_contribution", "type": "number", "label": "Capital Contribution", "required": True},
                {"key": "profit_split", "type": "text", "label": "Profit & Loss Split", "required": True},
                {"key": "management", "type": "select", "label": "Management Structure", "options": ["Equal management", "Managing partner", "By unanimous consent"], "required": True},
                {"key": "term_months", "type": "number", "label": "Term (months)", "required": True},
                {"key": "governing_law", "type": "text", "label": "Governing Law Jurisdiction", "required": True},
            ],
            "clauses": [
                "formation",
                "capital_contributions",
                "profit_and_loss",
                "management",
                "decision_making",
                "withdrawal_and_admission",
                "fiduciary_duties",
                "accounting",
                "dissolution",
                "dispute_resolution",
                "governing_law",
                "entire_agreement",
            ],
        },
    },
]


# ============================================================
# COMPANY POLICIES (Sample)
# ============================================================

COMPANY_POLICIES = [
    {
        "id": str(uuid.UUID("a0000000-0000-0000-0000-000000000001")),
        "name": "Maximum Liability Cap",
        "description": "Liability in any agreement must not exceed $5,000,000",
        "category": "financial",
        "clause_type": "maximum",
        "severity_if_missing": "critical",
        "is_active": True,
        "priority": 100,
        "rules": {
            "max_liability_cap": "5000000",
            "currency": "USD",
        },
    },
    {
        "id": str(uuid.UUID("a0000000-0000-0000-0000-000000000002")),
        "name": "Required Governing Law",
        "description": "All agreements must specify governing law",
        "category": "legal",
        "clause_type": "required",
        "severity_if_missing": "high",
        "is_active": True,
        "priority": 90,
        "rules": {},
    },
    {
        "id": str(uuid.UUID("a0000000-0000-0000-0000-000000000003")),
        "name": "Dispute Resolution Required",
        "description": "All agreements must include dispute resolution mechanism",
        "category": "legal",
        "clause_type": "required",
        "severity_if_missing": "high",
        "is_active": True,
        "priority": 85,
        "rules": {},
    },
    {
        "id": str(uuid.UUID("a0000000-0000-0000-0000-000000000004")),
        "name": "Minimum Confidentiality Period",
        "description": "Confidentiality period must be at least 1 year",
        "category": "confidentiality",
        "clause_type": "minimum",
        "severity_if_missing": "medium",
        "is_active": True,
        "priority": 70,
        "rules": {
            "min_confidentiality_period_days": 365,
        },
    },
    {
        "id": str(uuid.UUID("a0000000-0000-0000-0000-000000000005")),
        "name": "Minimum Notice Period",
        "description": "Termination notice must be at least 30 days",
        "category": "termination",
        "clause_type": "minimum",
        "severity_if_missing": "medium",
        "is_active": True,
        "priority": 60,
        "rules": {
            "min_notice_period_days": 30,
        },
    },
]


# ============================================================
# LANGUAGES
# ============================================================

LANGUAGES = [
    {"id": "lang-en", "code": "en", "name": "English", "native_name": "English", "locale": "en-US", "direction": "ltr", "is_active": True},
    {"id": "lang-si", "code": "si", "name": "Sinhala", "native_name": "සිංහල", "locale": "si-LK", "direction": "ltr", "is_active": True},
    {"id": "lang-ta", "code": "ta", "name": "Tamil", "native_name": "தமிழ்", "locale": "ta-LK", "direction": "ltr", "is_active": True},
    {"id": "lang-zh", "code": "zh", "name": "Chinese", "native_name": "中文", "locale": "zh-CN", "direction": "ltr", "is_active": True},
    {"id": "lang-ar", "code": "ar", "name": "Arabic", "native_name": "العربية", "locale": "ar-SA", "direction": "rtl", "is_active": True},
    {"id": "lang-hi", "code": "hi", "name": "Hindi", "native_name": "हिन्दी", "locale": "hi-IN", "direction": "ltr", "is_active": True},
    {"id": "lang-ja", "code": "ja", "name": "Japanese", "native_name": "日本語", "locale": "ja-JP", "direction": "ltr", "is_active": True},
    {"id": "lang-de", "code": "de", "name": "German", "native_name": "Deutsch", "locale": "de-DE", "direction": "ltr", "is_active": True},
    {"id": "lang-fr", "code": "fr", "name": "French", "native_name": "Français", "locale": "fr-FR", "direction": "ltr", "is_active": True},
    {"id": "lang-es", "code": "es", "name": "Spanish", "native_name": "Español", "locale": "es-ES", "direction": "ltr", "is_active": True},
]


# ============================================================
# LIFECYCLE STATES & TRANSITION RULES
# ============================================================

# Both lists are derived from the canonical domain state machine so the DB
# registry can never drift from the enum route code compares against.
from app.domain.agreement_states import (  # noqa: E402
    DEFAULT_TRANSITION_RULES as TRANSITION_RULES,
    state_registry_rows as _state_registry_rows,
)

AGREEMENT_STATES = _state_registry_rows()


# ============================================================
# SEED FUNCTION
# ============================================================

async def seed_jurisdictions(db):
    """Seed jurisdictions and their clause templates."""
    from sqlalchemy import select
    from app.models.jurisdiction import Jurisdiction, JurisdictionClause

    # Check if already seeded
    result = await db.execute(select(Jurisdiction).limit(1))
    if result.scalar_one_or_none():
        print("  ⏭️  Jurisdictions already exist, skipping...")
        return

    print("  🌍 Seeding jurisdictions...")
    for j_data in JURISDICTIONS:
        jurisdiction = Jurisdiction(
            id=uuid.UUID(j_data["id"]),
            code=j_data["code"],
            name=j_data["name"],
            region=j_data["region"],
            language=j_data["language"],
            legal_system=j_data["legal_system"],
            currency=j_data["currency"],
            timezone=j_data["timezone"],
            required_clauses=j_data["required_clauses"],
            prohibited_clauses=j_data["prohibited_clauses"],
            signature_requirements=j_data["signature_requirements"],
            default_dispute_resolution=j_data["default_dispute_resolution"],
            arbitration_institution=j_data["arbitration_institution"],
            default_confidentiality_period_years=j_data["default_confidentiality_period_years"],
            statute_of_limitations_years=j_data["statute_of_limitations_years"],
            is_active=j_data["is_active"],
        )
        db.add(jurisdiction)
    await db.flush()
    print(f"    ✅ {len(JURISDICTIONS)} jurisdictions created")

    print("  📜 Seeding jurisdiction clauses...")
    clause_count = 0
    for c_data in JURISDICTION_CLAUSES:
        clause = JurisdictionClause(
            id=uuid.uuid4(),
            jurisdiction_id=uuid.UUID(c_data["jurisdiction_id"]),
            clause_type=c_data["clause_type"],
            name=c_data["name"],
            description=c_data["description"],
            standard_text=c_data["standard_text"],
            alternative_texts=c_data.get("alternative_texts"),
            risk_level=c_data["risk_level"],
            is_mandatory=c_data["is_mandatory"],
            applies_to_types=c_data.get("applies_to_types", ["all"]),
        )
        db.add(clause)
        clause_count += 1
    await db.flush()
    print(f"    ✅ {clause_count} jurisdiction clauses created")


# ------------------------------------------------------------------
# Extended agreement-type catalog (spec §2–§13): the ~46 non-MVP types.
# Schemas are composed from shared question banks per agreement "kind" plus
# per-type extras, keeping this data compact while still data-driven.
# ------------------------------------------------------------------

def _q(key, label, qtype="text", required=True, **kw):
    item = {"key": key, "type": qtype, "label": label, "required": required}
    options = kw.pop("options", None)
    if options:
        item["options"] = options
    item.update(kw)
    return item


_COMMON_QUESTIONS = [
    _q("effective_date", "Effective Date", "date"),
    _q("party_a_name", "Party A (Full Legal Name)"),
    _q("party_a_address", "Party A Registered Address"),
    _q("party_b_name", "Party B (Full Legal Name)"),
    _q("party_b_address", "Party B Registered Address"),
    _q("purpose", "Purpose of the Agreement", "textarea"),
    _q("term_months", "Term (months)", "number"),
    _q("auto_renew", "Auto-Renew", "select", options=["Yes", "No"], required=False),
    _q("currency", "Currency", "select", options=["LKR", "USD", "EUR", "GBP", "SGD", "INR"]),
    _q("governing_law", "Governing Law Jurisdiction"),
    _q("dispute_resolution", "Dispute Resolution", "select", options=["Arbitration", "Mediation", "Litigation"]),
]

_KIND_SCHEMAS = {
    "talent": {
        "questions": [
            _q("start_date", "Start Date", "date"),
            _q("compensation", "Compensation / Remuneration", "textarea"),
            _q("notice_period_days", "Notice Period (days)", "number"),
        ],
        "clauses": [
            "roles_and_duties", "compensation", "confidentiality",
            "intellectual_property", "termination", "notice_period",
            "governing_law", "entire_agreement",
        ],
    },
    "technology": {
        "questions": [
            _q("scope_of_grant", "Scope of Grant / Permitted Use", "textarea"),
            _q("fees", "Fees / Royalties", "textarea"),
            _q("support_period_months", "Support & Maintenance (months)", "number", required=False),
            _q("warranties", "Representations & Warranties", "textarea", required=False),
            # Dynamic questionnaire (spec §4.2 / §15): personal-data branch.
            # When personal data is processed, the wizard reveals the DPA
            # fieldset so the rendered document always carries the right
            # data-processing clauses.
            _q(
                "personal_data_processed",
                "Will personal data be processed under this Agreement?",
                "select",
                options=["Yes", "No"],
            ),
            _q(
                "processing_purpose",
                "Purpose of Processing",
                "textarea",
                required=False,
                condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            ),
            _q(
                "data_categories",
                "Categories of Personal Data",
                "textarea",
                required=False,
                condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            ),
            _q(
                "data_subjects",
                "Categories of Data Subjects",
                "textarea",
                required=False,
                condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            ),
            _q(
                "cross_border_transfers",
                "Will personal data be transferred outside the jurisdiction?",
                "select",
                options=["Yes", "No"],
                required=False,
                condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            ),
            _q(
                "data_retention_months",
                "Data Retention Period (months)",
                "number",
                required=False,
                condition={"field": "personal_data_processed", "operator": "equals", "value": "Yes"},
            ),
        ],
        "clauses": [
            "license_grant", "ip_ownership", "confidentiality", "warranties",
            "limitation_of_liability", "termination", "governing_law",
            "dispute_resolution", "entire_agreement",
        ],
    },
    "financial": {
        "questions": [
            _q("principal_amount", "Principal Amount", "number"),
            _q("interest_rate", "Interest Rate (% p.a.)", "number"),
            _q("repayment_term_months", "Repayment Term (months)", "number"),
            _q("repayment_schedule", "Repayment Schedule", "textarea"),
        ],
        "clauses": [
            "loan_terms", "interest", "repayment_schedule", "covenants",
            "default", "security", "remedies", "governing_law",
            "dispute_resolution", "entire_agreement",
        ],
    },
    "realestate": {
        "questions": [
            _q("premises_address", "Premises Address"),
            _q("rent_amount", "Rent Amount", "number"),
            _q("rent_frequency", "Rent Frequency", "select", options=["Monthly", "Quarterly", "Annual"]),
            _q("lease_term_months", "Lease Term (months)", "number"),
            _q("security_deposit", "Security Deposit", "number", required=False),
            _q("maintenance_responsibility", "Maintenance Responsibility", "textarea", required=False),
        ],
        "clauses": [
            "premises", "term_and_duration", "rent_and_deposit", "maintenance",
            "use_of_premises", "termination", "governing_law",
            "dispute_resolution", "entire_agreement",
        ],
    },
    "marketing": {
        "questions": [
            _q("campaign_description", "Campaign / Deliverables Description", "textarea"),
            _q("campaign_period_months", "Campaign Period (months)", "number"),
            _q("campaign_fee", "Total Fee", "number"),
            _q("content_rights", "Content & Usage Rights", "textarea", required=False),
        ],
        "clauses": [
            "services_description", "payment_terms", "content_rights",
            "indemnity", "confidentiality", "term_and_duration",
            "limitation_of_liability", "governing_law", "dispute_resolution",
        ],
    },
    "supply_chain": {
        "questions": [
            _q("goods_description", "Goods / Services Description", "textarea"),
            _q("quantity", "Quantity", "text"),
            _q("unit_price", "Unit Price", "number", required=False),
            _q("delivery_terms", "Delivery Terms", "textarea"),
            _q("delivery_schedule", "Delivery Schedule", "textarea", required=False),
            _q("insurance_required", "Insurance Required", "select", options=["Yes", "No"]),
        ],
        "clauses": [
            "goods_services", "pricing", "delivery", "inspection_acceptance",
            "warranty", "force_majeure", "indemnity", "limitation_of_liability",
            "governing_law", "dispute_resolution",
        ],
    },
    "corporate": {
        "questions": [
            _q("transaction_value", "Transaction Value", "number", required=False),
            _q("closing_date", "Expected Closing Date", "date", required=False),
            _q("conditions_precedent", "Conditions Precedent", "textarea", required=False),
        ],
        "clauses": [
            "representations_warranties", "covenants", "closing_conditions",
            "indemnity", "confidentiality", "dispute_resolution",
            "governing_law", "entire_agreement",
        ],
    },
    "dispute": {
        "questions": [
            _q("dispute_subject", "Subject Matter of Dispute", "textarea"),
            _q("settlement_amount", "Settlement Amount", "number", required=False),
            _q("release_scope", "Scope of Release", "textarea", required=False),
            _q("non_admission", "Non-Admission of Liability", "select", options=["Yes", "No"]),
        ],
        "clauses": [
            "mutual_release", "settlement_payment", "confidentiality",
            "non_admission", "entire_agreement", "governing_law",
            "dispute_resolution",
        ],
    },
    # Spec §3.A: Corporate & Governance agreements.
    "governance": {
        "questions": [
            _q("company_name", "Company Name"),
            _q("company_registration_number", "Company Registration Number", required=False),
            _q("share_capital", "Share Capital / Capital Structure", "textarea", required=False),
            _q("signatory_name", "Authorized Signatory Name", required=False),
            _q("signatory_title", "Authorized Signatory Title", required=False),
        ],
        "clauses": [
            "governance_structure", "share_transfer", "reserved_matters",
            "board_representation", "confidentiality", "dispute_resolution",
            "governing_law", "entire_agreement",
        ],
    },
    # Spec §11: service levels that the obligation engine can monitor.
    "sla": {
        "questions": [
            _q("service_provider_name", "Service Provider Name"),
            _q("client_name", "Client Name"),
            _q("uptime_target", "Uptime Target (%)", "number", required=False),
            _q("response_time_hours", "Response Time (hours)", "number", required=False),
            _q("resolution_time_hours", "Resolution Time (hours)", "number", required=False),
            _q("support_hours", "Support Hours", "textarea", required=False),
            _q("severity_levels", "Severity Levels", "textarea", required=False),
            _q("service_credits", "Service Credits", "textarea", required=False),
            _q("escalation_contacts", "Escalation Contacts", "textarea", required=False),
        ],
        "clauses": [
            "service_description", "service_levels", "service_credits",
            "monitoring_and_reporting", "escalation", "term_and_duration",
            "limitation_of_liability", "governing_law", "dispute_resolution",
        ],
    },
}


# (key, name, category, kind, description, extras[])
EXTENDED_AGREEMENT_TYPES = [
    ("offer_letter", "Offer Letter", "employment", "talent",
     "Employment offer summarising role, compensation, and start terms", []),
    ("internship_agreement", "Internship Agreement", "employment", "talent",
     "Fixed-term arrangement for interns with stipend and supervision terms", [
        _q("intern_name", "Intern Name"),
        _q("university", "Institution / University"),
        _q("stipend", "Monthly Stipend", "number", required=False),
    ]),
    ("employee_ip_assignment", "Employee IP Assignment Agreement", "employment", "talent",
     "Assigns IP created during employment to the employer", [
        _q("ip_scope", "IP Covered by Assignment", "textarea"),
    ]),
    ("employee_nda", "Employee Non-Disclosure Agreement", "confidentiality", "talent",
     "One-way confidentiality for employees protecting employer information", [
        _q("confidentiality_period", "Confidentiality Period", "select",
           options=["1 year", "2 years", "3 years", "5 years", "Indefinite"]),
    ]),
    ("non_solicitation", "Non-Solicitation Agreement", "employment", "talent",
     "Restricts solicitation of clients and staff after termination, where enforceable", [
        _q("restricted_period_months", "Restricted Period (months)", "number"),
        _q("restricted_scope", "Restricted Scope", "textarea"),
    ]),
    ("software_license", "Software License Agreement", "software", "technology",
     "License of software to a licensee with permitted-use and support terms", [
        _q("license_type", "License Type", "select",
           options=["Perpetual", "Subscription", "Perpetual + Subscription"]),
        _q("seat_count", "Seat / Instance Count", "number", required=False),
    ]),
    ("api_agreement", "API Agreement", "software", "technology",
     "Terms governing consumption or provision of an API", [
        _q("api_description", "API Description"),
        _q("rate_limits", "Usage Limits / Rate Limits", "textarea", required=False),
    ]),
    ("data_processing_agreement", "Data Processing Agreement", "privacy", "technology",
     "GDPR/PDPA-compliant DPA governing personal-data processing", [
        _q("processing_purpose", "Purpose of Processing"),
        _q("data_categories", "Categories of Personal Data", "textarea"),
        _q("data_subjects", "Categories of Data Subjects", "textarea"),
        _q("data_transfers_abroad", "Cross-Border Transfers", "select",
           options=["Yes", "No"]),
    ]),
    ("ip_assignment", "IP Assignment Agreement", "ip", "technology",
     "Outright transfer of intellectual property between parties", [
        _q("ip_description", "IP Being Assigned", "textarea"),
        _q("consideration", "Consideration", "textarea"),
    ]),
    ("ip_license", "IP License Agreement", "ip", "technology",
     "License of intellectual property without transfer of ownership", [
        _q("ip_description", "IP Being Licensed", "textarea"),
        _q("license_exclusivity", "Exclusivity", "select",
           options=["Exclusive", "Sole", "Non-exclusive"]),
        _q("license_territory", "Territory", "textarea", required=False),
    ]),
    ("trademark_license", "Trademark License Agreement", "ip", "technology",
     "License to use a trademark with quality-control and approval terms", [
        _q("mark_details", "Trademark Details"),
        _q("license_territory", "Territory", "textarea", required=False),
        _q("quality_control", "Quality Control Standards", "textarea", required=False),
    ]),
    ("technology_transfer", "Technology Transfer Agreement", "technology", "technology",
     "Transfer of technology, know-how and supporting documentation", [
        _q("technology_description", "Technology / Know-How", "textarea"),
        _q("training_included", "Training Included", "select", options=["Yes", "No"]),
    ]),
    ("loan_agreement", "Loan Agreement", "financial", "financial",
     "Secured or unsecured loan with interest, repayment, and default terms", [
        _q("loan_secured", "Secured Loan", "select", options=["Yes", "No"]),
        _q("collateral", "Collateral / Security", "textarea", required=False),
    ]),
    ("promissory_note", "Promissory Note", "financial", "financial",
     "Unconditional written promise to pay a fixed sum on demand or at a date", [
        _q("payment_on_demand", "Payable on Demand", "select", options=["Yes", "No"]),
        _q("maturity_date", "Maturity Date", "date", required=False),
    ]),
    ("payment_agreement", "Payment Agreement", "financial", "financial",
     "Structurised repayment of an outstanding balance by instalments", []),
    ("credit_agreement", "Credit Agreement", "financial", "financial",
     "Credit facility terms including limit, drawdown, and fees", [
        _q("credit_limit", "Credit Limit", "number"),
        _q("drawdown_terms", "Drawdown Terms", "textarea", required=False),
    ]),
    ("guarantee", "Guarantee Agreement", "financial", "financial",
     "Guarantor's undertaking to answer for the obligations of a debtor", [
        _q("guarantor_name", "Guarantor Name"),
        _q("underlying_obligation", "Underlying Obligation", "textarea"),
    ]),
    ("debt_settlement", "Debt Settlement Agreement", "financial", "financial",
     "Settlement of a debt for less than the full balance", [
        _q("original_debt", "Original Debt Amount", "number"),
        _q("settlement_amount", "Settlement Amount", "number"),
    ]),
    ("lease_agreement", "Lease Agreement", "realestate", "realestate",
     "Residential or general lease of premises", []),
    ("commercial_lease", "Commercial Lease Agreement", "realestate", "realestate",
     "Lease of commercial premises with fitted-out obligations", []),
    ("property_management", "Property Management Agreement", "realestate", "realestate",
     "Appointment of a property manager for an owner", [
        _q("management_fee_pct", "Management Fee (% of rent)", "number"),
        _q("authorized_tasks", "Authorized Management Tasks", "textarea", required=False),
    ]),
    ("facility_use", "Facility Use Agreement", "realestate", "realestate",
     "Non-leasing grant of use of a facility or shared space", [
        _q("facility_description", "Facility Description"),
        _q("usage_hours", "Permitted Usage Hours", "textarea", required=False),
    ]),
    ("marketing_services", "Marketing Services Agreement", "marketing", "marketing",
     "Marketing, SEO, and lead-generation services engagement", [
        _q("kpis", "Deliverables & KPIs", "textarea"),
    ]),
    ("influencer", "Influencer Agreement", "marketing", "marketing",
     "Social-media creator engagement with content and disclosure terms", [
        _q("platform", "Platform(s)", "textarea"),
        _q("content_schedule", "Content Schedule", "textarea", required=False),
    ]),
    ("advertising", "Advertising Agreement", "marketing", "marketing",
     "Placement of advertising across media inventory", [
        _q("ad_inventory", "Ad Inventory / Placements", "textarea"),
    ]),
    ("sponsorship", "Sponsorship Agreement", "marketing", "marketing",
     "Sponsorship of an event, team, or property", [
        _q("event_description", "Event / Property", "textarea"),
        _q("brand_visibility", "Sponsor Visibility Rights", "textarea", required=False),
    ]),
    ("brand_ambassador", "Brand Ambassador Agreement", "marketing", "marketing",
     "Longer-term ambassador engagement with exclusivity and usage rights", [
        _q("exclusive", "Exclusivity", "select", options=["Yes", "No"]),
    ]),
    ("affiliate", "Affiliate Agreement", "marketing", "marketing",
     "Commission-based promotion of products through an affiliate", [
        _q("commission_rate", "Commission Rate (% or flat)", "textarea"),
        _q("commission_period", "Commission Period", "select",
           options=["Monthly", "Quarterly", "Per sale"]),
    ]),
    ("transportation", "Transportation Agreement", "logistics", "supply_chain",
     "Carriage of goods by road, air, or sea", [
        _q("route", "Route / Origin-Destination"),
        _q("freight_terms", "Freight Terms (Incoterms)", "textarea"),
    ]),
    ("logistics", "Logistics Agreement", "logistics", "supply_chain",
     "End-to-end logistics and freight-management services", [
        _q("service_levels", "Service Levels", "textarea", required=False),
    ]),
    ("warehousing", "Warehousing Agreement", "logistics", "supply_chain",
     "Storage, handling, and inventory services for stored goods", [
        _q("storage_capacity", "Storage Capacity", "text"),
        _q("handling_fees", "Handling / Storage Fees", "textarea", required=False),
    ]),
    ("manufacturing", "Manufacturing Agreement", "manufacturing", "supply_chain",
     "Contract manufacturing of goods to specification", [
        _q("specifications", "Product Specifications", "textarea"),
        _q("quality_standards", "Quality Standards", "textarea", required=False),
        _q("tooling_ownership", "Tooling Ownership", "textarea", required=False),
    ]),
    ("procurement", "Procurement Agreement", "procurement", "supply_chain",
     "Framework for purchase of goods/services from a supplier", [
        _q("framework_volume", "Estimated Purchase Volume", "textarea", required=False),
    ]),
    ("import_export", "Import / Export Agreement", "logistics", "supply_chain",
     "Cross-border trade terms incl. compliance, customs and title transfer", [
        _q("hs_codes", "Commodity / HS Codes", "textarea", required=False),
        _q("compliance", "Export/Import Compliance", "textarea", required=False),
    ]),
    ("merger", "Merger Agreement", "corporate", "corporate",
     "Combination of two entities into one", [
        _q("merger_mechanism", "Merger Mechanism", "textarea", required=False),
    ]),
    ("acquisition", "Acquisition Agreement", "corporate", "corporate",
     "Acquisition of shares or an entity", [
        _q("target_entity", "Target Entity", "textarea", required=False),
    ]),
    ("asset_purchase", "Asset Purchase Agreement", "corporate", "corporate",
     "Purchase of a business's assets rather than its equity", [
        _q("assets_included", "Assets Included", "textarea"),
        _q("liabilities_assumed", "Liabilities Assumed", "textarea", required=False),
    ]),
    ("joint_venture", "Joint Venture Agreement", "corporate", "corporate",
     "Formation and operation of a jointly-controlled venture", [
        _q("venture_name", "Venture Name"),
        _q("contribution_split", "Contributions & Ownership Split", "textarea"),
    ]),
    ("strategic_alliance", "Strategic Alliance Agreement", "corporate", "corporate",
     "Co-operation framework falling short of a formal joint venture", [
        _q("alliance_scope", "Alliance Scope", "textarea"),
    ]),
    ("strategic_partnership", "Strategic Partnership Agreement", "commercial", "corporate",
     "Longer-term commercial co-operation between two businesses (spec §3 #18)", [
        _q("partnership_objectives", "Partnership Objectives", "textarea"),
        _q("contributions", "Contributions of Each Party", "textarea"),
        _q("governance_committee", "Governance / Steering Committee", "textarea", required=False),
        _q("revenue_sharing", "Revenue / Cost Sharing", "textarea", required=False),
        _q("exclusivity", "Exclusivity", "select",
           options=["Exclusive", "Semi-exclusive", "Non-exclusive"]),
        _q("term_years", "Initial Term (years)", "number"),
    ]),
    ("settlement", "Settlement Agreement", "dispute", "dispute",
     "Mutual settlement of a dispute with mutual release", []),
    ("mediation", "Mediation Agreement", "dispute", "dispute",
     "Agreement to refer a dispute to mediation", [
        _q("mediation_process", "Mediation Process", "textarea", required=False),
    ]),
    ("arbitration", "Arbitration Agreement", "dispute", "dispute",
     "Agreement to resolve a dispute by arbitration", [
        _q("arbitral_body", "Arbitral Body / Rules", "textarea"),
    ]),
    ("release_waiver", "Release / Waiver Agreement", "dispute", "dispute",
     "Waiver and release of claims in exchange for consideration", [
        _q("claims_released", "Claims Being Released", "textarea"),
    ]),
    # --- Spec §3.A: Corporate & Governance (previously missing) ---
    ("shareholders_agreement", "Shareholders Agreement", "corporate", "governance",
     "Rights of shareholders: voting, transfers, reserved matters and exit", [
        _q("tag_along_rights", "Tag-Along Rights", "select", options=["Yes", "No"], required=False),
        _q("drag_along_rights", "Drag-Along Rights", "select", options=["Yes", "No"], required=False),
        _q("dividend_policy", "Dividend Policy", "textarea", required=False),
        _q("deadlock_resolution", "Deadlock Resolution Mechanism", "textarea", required=False),
    ]),
    ("share_purchase", "Share Purchase Agreement", "corporate", "governance",
     "Sale and purchase of shares with warranties and completion terms", [
        _q("purchase_price", "Purchase Price", "number", required=False),
        _q("share_class", "Class / Number of Shares", "text", required=False),
        _q("completion_date", "Expected Completion Date", "date", required=False),
    ]),
    ("investment_agreement", "Investment Agreement", "corporate", "governance",
     "Subscription for equity with investor rights and protections", [
        _q("investment_amount", "Investment Amount", "number", required=False),
        _q("valuation", "Pre-Money Valuation", "number", required=False),
        _q("liquidation_preference", "Liquidation Preference", "textarea", required=False),
        _q("anti_dilution", "Anti-Dilution Protection", "select", options=["Yes", "No"], required=False),
    ]),
    ("convertible_note", "Convertible Note Agreement", "corporate", "governance",
     "Loan that converts into equity on defined trigger events", [
        _q("note_principal", "Note Principal", "number", required=False),
        _q("interest_rate", "Interest Rate (% p.a.)", "number", required=False),
        _q("conversion_trigger", "Conversion Trigger", "textarea", required=False),
        _q("valuation_cap", "Valuation Cap", "number", required=False),
        _q("discount_rate", "Conversion Discount (%)", "number", required=False),
    ]),
    ("safe_investment", "SAFE-style Investment Agreement", "corporate", "governance",
     "Simple agreement for future equity, subject to jurisdiction-specific drafting", [
        _q("safe_amount", "SAFE Amount", "number", required=False),
        _q("valuation_cap", "Valuation Cap", "number", required=False),
    ]),
    ("founder_agreement", "Founder Agreement", "corporate", "governance",
     "Founder roles, vesting, IP and departure provisions", [
        _q("vesting_schedule", "Vesting Schedule", "textarea", required=False),
        _q("cliff_months", "Cliff Period (months)", "number", required=False),
        _q("leaver_provisions", "Good/Bad Leaver Provisions", "textarea", required=False),
    ]),
    ("board_resolution", "Board Resolution", "governance", "governance",
     "Formal written resolution of the board of directors", [
        _q("resolution_subject", "Subject of the Resolution", "textarea"),
        _q("resolution_text", "Resolved That", "textarea"),
        _q("directors_present", "Directors Present", "textarea", required=False),
        _q("meeting_date", "Meeting / Resolution Date", "date", required=False),
    ]),
    # --- Spec §9-20: commercial types previously missing ---
    ("service_level_agreement", "Service Level Agreement", "services", "sla",
     "Measurable service performance targets with credits and escalation", []),
    ("purchase_agreement", "Purchase Agreement", "procurement", "supply_chain",
     "One-off purchase of goods or services between buyer and seller", []),
    ("distribution", "Distribution Agreement", "commercial", "supply_chain",
     "Appointment of a distributor with territory and exclusivity terms", [
        _q("territory", "Territory", "textarea", required=False),
        _q("exclusivity", "Exclusivity", "select",
           options=["Exclusive", "Semi-exclusive", "Non-exclusive"]),
        _q("minimum_purchases", "Minimum Purchase Commitment", "textarea", required=False),
    ]),
    ("reseller", "Reseller Agreement", "commercial", "supply_chain",
     "Right to resell products or software with pricing and support terms", [
        _q("reseller_margin", "Reseller Margin / Discount", "textarea", required=False),
    ]),
    ("referral", "Referral Agreement", "commercial", "marketing",
     "Referral of customers in exchange for a commission", [
        _q("commission_rate", "Commission Rate (% or flat)", "textarea", required=False),
        _q("attribution_window", "Attribution Window", "textarea", required=False),
    ]),
    ("commission", "Commission Agreement", "commercial", "marketing",
     "Commission terms for sales agents and representatives", [
        _q("commission_basis", "Commission Basis", "textarea", required=False),
        _q("payment_trigger", "Payment Trigger", "textarea", required=False),
    ]),
]


def compose_catalog_schema(kind: str, extras: list) -> dict:
    schema = {
        "questions": [dict(q) for q in _COMMON_QUESTIONS],
        "clauses": list(_KIND_SCHEMAS[kind]["clauses"]),
    }
    schema["questions"].extend(dict(q) for q in _KIND_SCHEMAS[kind]["questions"])
    schema["questions"].extend(dict(q) for q in extras)
    return schema


def catalog_type_id(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"contractos://agreement-type/{key}")


async def seed_catalog_agreement_types(db):
    """Upsert the extended agreement-type catalog idempotently by key."""
    from sqlalchemy import select
    from app.models.agreement_type import AgreementType

    existing = await db.execute(select(AgreementType.key))
    keys = {row[0] for row in existing.all()}

    # Types whose agreements are not bilateral documents get a dedicated
    # template (spec §3.A.7: a Board Resolution is a corporate record with
    # resolution text and a certification block, not party-signature blocks).
    _TYPE_TEMPLATE_OVERRIDES = {
        "board_resolution": "board_resolution_lk_v1",
    }

    # Every catalog kind has a purpose-built contract template (spec §3.A):
    # all 57 catalog agreement types resolve to a domain template instead of
    # the generic fallback. Kinds whose agreements are corporate records
    # (governance) still get the governance template except where the
    # dedicated per-type override above applies.
    _KIND_TEMPLATE_KEYS = {
        "talent": "talent_agreement_lk_v1",
        "technology": "technology_agreement_lk_v1",
        "financial": "financial_agreement_lk_v1",
        "realestate": "realestate_agreement_lk_v1",
        "marketing": "marketing_agreement_lk_v1",
        "supply_chain": "supply_chain_agreement_lk_v1",
        "corporate": "corporate_agreement_lk_v1",
        "governance": "governance_agreement_lk_v1",
        "dispute": "dispute_agreement_lk_v1",
        "sla": "sla_agreement_lk_v1",
    }

    def _template_key_for(key: str, kind: str) -> str:
        return _TYPE_TEMPLATE_OVERRIDES.get(
            key,
            _KIND_TEMPLATE_KEYS.get(kind, "generic_agreement_lk_v1"),
        )

    added = 0
    backfilled = 0
    for key, name, category, kind, description, extras in EXTENDED_AGREEMENT_TYPES:
        template_key = _template_key_for(key, kind)
        existing_row = None
        if key in keys:
            existing_row = (
                await db.execute(select(AgreementType).where(AgreementType.key == key))
            ).scalars().first()
            # Backfill legacy rows still using the generic fallback so every
            # catalog type renders its dedicated kind template.
            if (
                existing_row is not None
                and existing_row.template_key == "generic_agreement_lk_v1"
            ):
                existing_row.template_key = template_key
                backfilled += 1
            continue
        db.add(AgreementType(
            id=catalog_type_id(key),
            key=key,
            name=name,
            description=description,
            category=category,
            status="active",
            version=1,
            schema=compose_catalog_schema(kind, extras),
            template_key=template_key,
        ))
        added += 1
    await db.flush()
    print(
        f"    ✅ {added} catalog agreement types created "
        f"({len(EXTENDED_AGREEMENT_TYPES)} in catalog), {backfilled} template backfilled"
    )


# ------------------------------------------------------------------
# Additional jurisdictions & clauses (spec §9 real estate cautions,
# §12 corporate ops, shipping partners). Idempotent via uuid5 ids.
# ------------------------------------------------------------------

EXTRA_JURISDICTIONS = [
    {
        "code": "AU",
        "name": "Australia",
        "region": "Oceania",
        "language": "en",
        "legal_system": "Common Law",
        "currency": "AUD",
        "timezone": "Australia/Sydney",
        "required_clauses": ["governing_law", "dispute_resolution"],
        "prohibited_clauses": [],
        "signature_requirements": {
            "min_witnesses": 0,
            "requires_notarization": False,
            "requires_stamp_duty": False,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "litigation",
        "arbitration_institution": None,
        "default_confidentiality_period_years": 3,
        "statute_of_limitations_years": 6,
        "is_active": True,
    },
    {
        "code": "AE",
        "name": "United Arab Emirates",
        "region": "Middle East",
        "language": "ar",
        "legal_system": "Mixed",
        "currency": "AED",
        "timezone": "Asia/Dubai",
        "required_clauses": ["governing_law", "dispute_resolution"],
        "prohibited_clauses": [],
        "signature_requirements": {
            "min_witnesses": 2,
            "requires_notarization": True,
            "requires_stamp_duty": False,
            "electronic_signatures_valid": True,
        },
        "default_dispute_resolution": "arbitration",
        "arbitration_institution": "Dubai International Arbitration Centre",
        "default_confidentiality_period_years": 3,
        "statute_of_limitations_years": 3,
        "is_active": True,
    },
]

# (jurisdiction_code, clause_type, name, text, risk_level, mandatory)
EXTRA_JURISDICTION_CLAUSES = [
    ("AU", "governing_law",
     "Governing Law (Australia)",
     "This Agreement shall be governed by and construed in accordance with the laws of the State of New South Wales, Australia.",
     "low", True),
    ("AU", "dispute_resolution",
     "Dispute Resolution (Australia)",
     "Any dispute arising out of or in connection with this Agreement shall be referred to and finally resolved by arbitration in accordance with the rules of the Australian Centre for International Commercial Arbitration (ACICA).",
     "low", True),
    ("AE", "governing_law",
     "Governing Law (UAE)",
     "This Agreement shall be governed by and construed in accordance with the laws of the Emirate of Dubai and the applicable federal laws of the United Arab Emirates.",
     "low", True),
    ("AE", "dispute_resolution",
     "Dispute Resolution (DIAC, UAE)",
     "Any dispute arising out of or in connection with this Agreement shall be referred to and finally resolved by arbitration in accordance with the Rules of the Dubai International Arbitration Centre (DIAC).",
     "low", True),
    ("LK", "payment_terms",
     "Payment & Late Interest (Sri Lanka)",
     "Unless otherwise agreed, all invoices are payable within thirty (30) days. Late payments shall accrue interest at a rate of 1.5% per month on the amount outstanding.",
     "low", False),
    ("LK", "termination_for_convenience",
     "Termination for Convenience (Sri Lanka)",
     "Either Party may terminate this Agreement for convenience upon sixty (60) days' prior written notice to the other Party.",
     "medium", False),
    ("SG", "payment_terms",
     "Payment & Late Interest (Singapore)",
     "Unless otherwise agreed, all invoices are payable within thirty (30) days. Late payments shall accrue interest at the rate of 1% per month on the amount outstanding.",
     "low", False),
    ("SG", "termination_for_convenience",
     "Termination for Convenience (Singapore)",
     "Either Party may terminate this Agreement for convenience upon sixty (60) days' prior written notice to the other Party.",
     "medium", False),
    ("GB", "limitation_of_liability",
     "Limitation of Liability (UK)",
     "Neither Party shall be liable to the other for any indirect or consequential loss. Each Party's aggregate liability under this Agreement shall not exceed the total fees paid or payable under this Agreement in the twelve (12) months preceding the claim.",
     "low", False),
    ("IN", "payment_terms",
     "Payment & Late Interest (India)",
     "Unless otherwise agreed, all invoices are payable within thirty (30) days. Late payments shall accrue interest at a rate not exceeding the applicable statutory rate.",
     "low", False),
]


def content_id(ns: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"contractos://{ns}/{key}")


async def seed_additional_jurisdictions(db):
    """Upsert extra jurisdictions and their clauses idempotently."""
    from sqlalchemy import select
    from app.models.jurisdiction import Jurisdiction, JurisdictionClause

    # Resolve jurisdiction ids by code from the database: base jurisdictions
    # are seeded with fixed ids elsewhere, so computing ids here would
    # violate the FK on real Postgres (tests use SQLite, which does not
    # enforce FKs, so this only surfaced during a real re-seed).
    existing_j = await db.execute(select(Jurisdiction.code, Jurisdiction.id))
    code_to_id: dict[str, object] = {row[0]: row[1] for row in existing_j.all()}

    j_added = 0
    for j in EXTRA_JURISDICTIONS:
        if j["code"] in code_to_id:
            continue
        jid = content_id("jurisdiction", j["code"])
        db.add(Jurisdiction(
            id=jid,
            code=j["code"],
            name=j["name"],
            region=j["region"],
            language=j["language"],
            legal_system=j["legal_system"],
            currency=j["currency"],
            timezone=j["timezone"],
            required_clauses=j["required_clauses"],
            prohibited_clauses=j["prohibited_clauses"],
            signature_requirements=j["signature_requirements"],
            default_dispute_resolution=j["default_dispute_resolution"],
            arbitration_institution=j["arbitration_institution"],
            default_confidentiality_period_years=j["default_confidentiality_period_years"],
            statute_of_limitations_years=j["statute_of_limitations_years"],
            is_active=j["is_active"],
        ))
        code_to_id[j["code"]] = jid
        j_added += 1
    await db.flush()

    existing_c = await db.execute(
        select(JurisdictionClause.jurisdiction_id, JurisdictionClause.clause_type)
    )
    have = {(row[0], row[1]) for row in existing_c.all()}

    c_added = 0
    skipped: list[str] = []
    for code, ctype, name, text, risk, mandatory in EXTRA_JURISDICTION_CLAUSES:
        jid = code_to_id.get(code)
        if jid is None:
            skipped.append(f"{code}/{ctype}")
            continue
        if (jid, ctype) in have:
            continue
        db.add(JurisdictionClause(
            id=content_id(f"jurisdiction-clause/{code}", ctype),
            jurisdiction_id=jid,
            clause_type=ctype,
            name=name,
            description=f"Standard {ctype.replace('_', ' ')} clause for {code}",
            standard_text=text,
            risk_level=risk,
            is_mandatory=mandatory,
            applies_to_types=["all"],
        ))
        c_added += 1
    await db.flush()
    if skipped:
        print(
            f"    ⚠️  skipped {len(skipped)} clauses for unknown jurisdictions: "
            f"{sorted(set(skipped))}"
        )
    print(f"    ✅ {j_added} jurisdictions + {c_added} jurisdiction clauses created")


async def seed_agreement_types(db):
    """Seed agreement types."""
    from sqlalchemy import select
    from app.models.agreement_type import AgreementType

    result = await db.execute(select(AgreementType).limit(1))
    if result.scalar_one_or_none():
        print("  ⏭️  Agreement types already exist, skipping...")
        return

    print("  📄 Seeding agreement types...")
    for at_data in AGREEMENT_TYPES:
        atype = AgreementType(
            id=uuid.UUID(at_data["id"]),
            key=at_data["key"],
            name=at_data["name"],
            description=at_data["description"],
            category=at_data["category"],
            status=at_data["status"],
            version=at_data["version"],
            schema=at_data["schema"],
            template_key=at_data.get("template_key"),
        )
        db.add(atype)
    await db.flush()
    print(f"    ✅ {len(AGREEMENT_TYPES)} agreement types created")


async def seed_company_policies(db):
    """Seed sample company policies."""
    from sqlalchemy import select
    from app.models.company_policy import CompanyPolicy
    from app.models.organization import Organization

    result = await db.execute(select(CompanyPolicy).limit(1))
    if result.scalar_one_or_none():
        print("  ⏭️  Company policies already exist, skipping...")
        return

    # Get first organization to attach policies to
    org_result = await db.execute(select(Organization).limit(1))
    org = org_result.scalar_one_or_none()
    org_id = org.id if org else None

    if not org_id:
        print("  ⚠️  No organization found, skipping company policies")
        return

    print(f"  📋 Seeding company policies for org {org_id}...")
    for p_data in COMPANY_POLICIES:
        policy = CompanyPolicy(
            id=uuid.UUID(p_data["id"]),
            organization_id=org_id,
            name=p_data["name"],
            description=p_data["description"],
            category=p_data["category"],
            clause_type=p_data["clause_type"],
            severity_if_missing=p_data["severity_if_missing"],
            is_active=p_data["is_active"],
            priority=p_data["priority"],
            rules=p_data["rules"],
        )
        db.add(policy)
    await db.flush()
    print(f"    ✅ {len(COMPANY_POLICIES)} company policies created")


async def seed_languages(db):
    """Seed supported languages."""
    from sqlalchemy import select
    from app.models.i18n import Language, LanguageDirection

    result = await db.execute(select(Language).limit(1))
    if result.scalar_one_or_none():
        print("  ⏭️  Languages already exist, skipping...")
        return

    print("  🌐 Seeding languages...")
    for l_data in LANGUAGES:
        language = Language(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"contractos:{l_data['code']}"),
            code=l_data["code"],
            name=l_data["name"],
            native_name=l_data["native_name"],
            locale=l_data["locale"],
            direction=(
                LanguageDirection.LTR if l_data["direction"] == "ltr" else LanguageDirection.RTL
            ),
            is_active=l_data["is_active"],
        )
        db.add(language)
    await db.flush()
    print(f"    ✅ {len(LANGUAGES)} languages created")


async def seed_extended_agreement_types(db):
    """Add the Tier-0 agreement types missing from an existing database.

    The base seed only inserted the first 4 MVP types; this upserts the
    remaining types idempotently by key so migrations don't lose them.
    """
    from sqlalchemy import select
    from app.models.agreement_type import AgreementType

    existing = await db.execute(select(AgreementType))
    by_key = {row.key: row for row in existing.scalars().all()}

    added = 0
    updated = 0
    for at_data in AGREEMENT_TYPES:
        if at_data["key"] not in by_key:
            atype = AgreementType(
                id=uuid.UUID(at_data["id"]),
                key=at_data["key"],
                name=at_data["name"],
                description=at_data["description"],
                category=at_data["category"],
                status=at_data["status"],
                version=at_data["version"],
                schema=at_data["schema"],
                template_key=at_data.get("template_key"),
            )
            db.add(atype)
            added += 1
        elif at_data.get("template_key") and not by_key[at_data["key"]].template_key:
            by_key[at_data["key"]].template_key = at_data["template_key"]
            updated += 1
    await db.flush()
    print(f"    ✅ {added} additional agreement types created, {updated} template keys backfilled")


async def seed_lifecycle(db):
    """Seed lifecycle states and transition rules (idempotent)."""
    from sqlalchemy import select
    from app.models.lifecycle import AgreementState, StatusTransitionRule

    existing = await db.execute(select(AgreementState.status))
    state_keys = {row[0] for row in existing.all()}

    for s in AGREEMENT_STATES:
        if s["status"] in state_keys:
            continue
        db.add(AgreementState(
            status=s["status"],
            label=s["label"],
            is_terminal=s["is_terminal"],
            description=s["description"],
            is_system=True,
        ))
    await db.flush()

    existing = await db.execute(select(StatusTransitionRule.action_key, StatusTransitionRule.from_status, StatusTransitionRule.to_status))
    have = {(r[0], r[1], r[2]) for r in existing.all()}

    count = 0
    for r in TRANSITION_RULES:
        if (r["action_key"], r["from_status"], r["to_status"]) in have:
            continue
        db.add(StatusTransitionRule(
            action_key=r["action_key"],
            from_status=r["from_status"],
            to_status=r["to_status"],
            description=r["description"],
            required_permission=r.get("required_permission"),
            allowed_conditions=r.get("conditions"),
            is_system=True,
        ))
        count += 1
    await db.flush()
    if count:
        print(f"    ✅ {count} transition rules created")
    else:
        print("    ⏭️  Transition rules already present")


# Canonical RBAC permission catalogue (spec §51). Route handlers enforce these
# via app.dependencies.rbac.require_permission; owner/admin roles hold all.
PERMISSION_CATALOG = [
    {"key": "agreement.view", "description": "View agreements in the organisation"},
    {"key": "agreement.send", "description": "Send agreements to counterparties"},
    {"key": "agreement.submit_approval", "description": "Route agreements into the approval chain"},
    {"key": "agreement.approve", "description": "Approve agreements at an approval stage"},
    {"key": "agreement.sign", "description": "Sign agreements on behalf of the organisation"},
    {"key": "agreement.request_signature", "description": "Start signature collection"},
    {"key": "agreement.terminate", "description": "Initiate and complete terminations"},
    {"key": "agreement.amend", "description": "Create and activate amendments"},
    {"key": "agreement.export", "description": "Export agreement data and evidence"},
    {"key": "agreement.manage_participants", "description": "Manage internal participants and external parties"},
    {"key": "agreement.propose_change", "description": "Propose negotiation changes"},
    {"key": "agreement.comment", "description": "Comment on agreements"},
    {"key": "template.manage", "description": "Create, edit, version and delete templates"},
    {"key": "clause.create", "description": "Create clause library entries"},
    {"key": "policy.manage", "description": "Manage company policy rules"},
    {"key": "retention.manage", "description": "Manage retention policies and legal holds"},
    {"key": "org.manage_roles", "description": "Create roles and assign permissions"},
    {"key": "org.manage_members", "description": "Add, remove and re-role organisation members"},
    {"key": "org.manage_sso", "description": "Configure SSO / SCIM connections"},
    {"key": "integration.manage", "description": "Configure enterprise integrations"},
    {"key": "webhook.manage", "description": "Manage outbound webhooks"},
    {"key": "legal_entity.manage", "description": "Manage legal entities and authorised signatories"},
    {"key": "workflow.view", "description": "View workflow definitions and instances"},
    {"key": "workflow.manage", "description": "Create, edit, validate, publish and disable workflow definitions"},
    {"key": "workflow.execute", "description": "Dispatch workflow events and complete tasks"},
    {"key": "workflow.resolve_incident", "description": "Resolve workflow incidents"},
    {"key": "monitoring.manage", "description": "Configure monitoring integrations and credentials"},
    {"key": "monitoring.manage_rules", "description": "Create, edit, pause and delete obligation monitoring rules"},
    {"key": "monitoring.view", "description": "View monitoring definitions, health and evaluation history"},
    {"key": "monitoring.view_data", "description": "View external observations and evidence attached to monitorings"},
]


async def seed_permissions(db):
    """Seed the RBAC permission catalogue (spec §51 / 24.8)."""
    from app.models.rbac import Permission
    from sqlalchemy import select

    created = 0
    for perm in PERMISSION_CATALOG:
        existing = await db.execute(
            select(Permission).where(Permission.key == perm["key"])
        )
        if existing.scalar_one_or_none() is None:
            db.add(Permission(
                key=perm["key"],
                description=perm["description"],
            ))
            created += 1
    if created:
        print(f"    ✅ {created} permissions created")
    else:
        print("    ⏭️  Permissions already present")


async def main():
    """Run all seed functions."""
    print("=" * 60)
    print("  ContractOS Database Seeder")
    print("=" * 60)
    print()

    async with AsyncSessionLocal() as db:
        try:
            await seed_permissions(db)
            await seed_jurisdictions(db)
            await seed_agreement_types(db)
            await seed_extended_agreement_types(db)
            await seed_catalog_agreement_types(db)
            await seed_additional_jurisdictions(db)
            await seed_company_policies(db)
            await seed_languages(db)
            await seed_lifecycle(db)
            await db.commit()
            print()
            print("=" * 60)
            print("  ✅ All seed data applied successfully!")
            print("=" * 60)
        except Exception as e:
            await db.rollback()
            print()
            print("=" * 60)
            print(f"  ❌ Error: {e}")
            print("=" * 60)
            raise


if __name__ == "__main__":
    asyncio.run(main())
