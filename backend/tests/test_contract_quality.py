"""Contract quality engine tests (spec 77-81)."""

from datetime import date

from app.services.contract_quality import (
    CrossReferenceValidator,
    DateConsistencyEngine,
    NumericalConsistencyEngine,
    PartyConsistencyEngine,
    UndefinedTermDetector,
    run_quality_checks,
)


DOC = """
MASTER SERVICES AGREEMENT

1. Definitions
1.1 "Confidential Information" means non-public information.
1.2 "Services" means the services described in Schedule A.

2. Service Levels
The Supplier shall meet the SLA at all times.

3. Term
This Agreement starts on 1 January 2027 and continues for 24 months.

4. Payment
30% of the price on signing, 40% on delivery, and 40% on acceptance.

5. Miscellaneous
Further obligations are set out in Section 9.
See Schedule B for the form of purchase order.
"""


def test_undefined_term_detected():
    findings = UndefinedTermDetector().detect(DOC)
    codes = [f.code for f in findings]
    assert "UNDEFINED_TERM" in codes
    sla = next(f for f in findings if "SLA" in f.message)
    assert sla.location is not None


def test_defined_terms_not_flagged():
    findings = UndefinedTermDetector().detect(DOC)
    messages = " | ".join(f.message for f in findings)
    assert "Confidential Information" not in messages
    assert "Services" not in messages or "means" in DOC


def test_cross_reference_missing_section():
    findings = CrossReferenceValidator().validate(DOC)
    assert any(
        f.code == "SECTION_REF_MISSING" and "section 9" in f.message.lower() for f in findings
    )


def test_missing_schedule_attachment_flagged_high():
    findings = CrossReferenceValidator().validate(DOC)
    missing = [f for f in findings if f.code == "ATTACHMENT_MISSING"]
    assert any("Schedule B" in f.message for f in missing)
    assert all(f.severity == "high" for f in missing)
    # Schedule A is declared, must not be flagged
    assert not any("Schedule A" in f.message for f in missing)


def test_percentage_mismatch_detected():
    bad = "Payment: 30% on signing, 40% on delivery, 40% on acceptance."
    findings = NumericalConsistencyEngine().check(bad)
    assert findings
    assert "110" in findings[0].message

    good = "Payment: 30% on signing, 40% on delivery, and 30% on acceptance."
    assert not NumericalConsistencyEngine().check(good)


def test_date_consistency_effective_after_expiry():
    findings = DateConsistencyEngine().check(
        effective_date=date(2027, 6, 1), expiry_date=date(2027, 1, 1)
    )
    assert any(f.code == "EFFECTIVE_AFTER_EXPIRY" for f in findings)
    assert findings[0].severity == "high"


def test_date_consistency_sow_exceeds_parent():
    findings = DateConsistencyEngine().check(
        doc_type="sow",
        expiry_date=date(2028, 1, 1),
        parent_expiry_date=date(2027, 12, 31),
    )
    assert any(f.code == "TERM_EXCEEDS_PARENT" for f in findings)


def test_party_consistency():
    findings = PartyConsistencyEngine().check(
        parties=["ABC Software (Private) Limited", "Global Buyer Inc"],
        signers=["ABC Holdings Ltd"],
    )
    assert findings
    assert findings[0].code == "SIGNING_ENTITY_MISMATCH"

    ok = PartyConsistencyEngine().check(
        parties=["ABC Software (Private) Limited"],
        signers=["ABC Software Pvt Ltd"],
    )
    assert not ok


def test_orchestrator_runs_all_engines():
    report = run_quality_checks(
        DOC,
        agreement_meta={
            "effective_date": "2027-06-01",
            "expiry_date": "2027-01-01",
            "parties": ["Acme Ltd"],
            "signers": ["Other Holdings Ltd"],
        },
    )
    engines = {f["engine"] for f in report["findings"]}
    assert "date_consistency" in engines
    assert "party_consistency" in engines
    assert report["has_blockers"]
