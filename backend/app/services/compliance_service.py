"""
Policy Compliance Service - Detect contract deviations from company policies.

Compares contract content against company-approved policies and standards.
"""

import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_analysis import RiskFinding
from app.models.agreement import Agreement, AgreementVersion
from app.models.company_policy import (
    CompanyPolicy,
    ComplianceReport,
    PolicyViolation,
)
from app.services.alerting_service import AlertSeverity, get_alerting_service
from app.services.escalation_service import EscalationLevel, get_escalation_service


class ComplianceService:
    """Detects policy deviations in contracts."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_compliance(
        self,
        agreement_id: UUID,
        organization_id: UUID,
        version_id: Optional[UUID] = None,
        checked_by: Optional[UUID] = None,
        send_alerts: bool = True,
    ) -> dict:
        """
        Run full compliance check against all active policies.

        Returns a compliance report with violations.
        When send_alerts=True, dispatches Slack/PagerDuty alerts for critical violations.
        """
        # Get agreement content
        version = None
        if version_id:
            result = await self.db.execute(
                select(AgreementVersion).where(AgreementVersion.id == version_id)
            )
            version = result.scalar_one_or_none()

        if version is None:
            result = await self.db.execute(
                select(AgreementVersion)
                .where(AgreementVersion.agreement_id == agreement_id)
                .order_by(AgreementVersion.version_number.desc())
                .limit(1)
            )
            version = result.scalar_one_or_none()

        if version is None or not version.content:
            raise ValueError("No rendered content available for compliance check")

        content = version.content
        content_lower = content.lower()

        # Get active policies for this organization
        result = await self.db.execute(
            select(CompanyPolicy).where(
                CompanyPolicy.organization_id == organization_id,
                CompanyPolicy.is_active == True,
            ).order_by(CompanyPolicy.priority.desc())
        )
        policies = list(result.scalars().all())

        # Resolve the agreement type key so we can filter policies.
        from app.models.agreement_type import AgreementType

        agr_result = await self.db.execute(
            select(Agreement.id, Agreement.agreement_type_id).where(
                Agreement.id == agreement_id
            )
        )
        agr_row = agr_result.one_or_none()
        agreement_type_key: str | None = None
        if agr_row and agr_row.agreement_type_id:
            atype_result = await self.db.execute(
                select(AgreementType.key).where(
                    AgreementType.id == agr_row.agreement_type_id
                )
            )
            agreement_type_key = atype_result.scalar_one_or_none()

        # Check each policy
        violations = []
        policies_checked = 0

        for policy in policies:
            # Skip policies that don't apply to this agreement type.
            # Mirrors clause_suggestion filtering: "all" matches every type,
            # and a missing ``applies_to_types`` also means universal.
            if policy.applies_to_types and agreement_type_key:
                if (
                    agreement_type_key not in policy.applies_to_types
                    and "all" not in policy.applies_to_types
                ):
                    continue

            policies_checked += 1
            policy_violations = await self._check_policy(policy, content, content_lower)
            violations.extend(policy_violations)

        # Save violations
        for v in violations:
            self.db.add(v)

        # Calculate score
        critical_count = sum(1 for v in violations if v.severity == "critical")
        high_count = sum(1 for v in violations if v.severity == "high")
        medium_count = sum(1 for v in violations if v.severity == "medium")
        low_count = sum(1 for v in violations if v.severity == "low")

        # Score: 100 - weighted penalty
        penalty = (
            critical_count * 25
            + high_count * 15
            + medium_count * 8
            + low_count * 3
        )
        score = max(0, 100.0 - penalty)

        # Create report
        report = ComplianceReport(
            agreement_id=agreement_id,
            version_id=version.id if version else None,
            total_policies_checked=policies_checked,
            violations_found=len(violations),
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            compliance_score=score,
            summary=self._generate_summary(
                policies_checked, len(violations), critical_count, high_count, score
            ),
            checked_by=checked_by,
        )
        self.db.add(report)

        await self.db.flush()
        await self.db.refresh(report)

        # Dispatch alerts for critical / high violations
        if send_alerts and (critical_count > 0 or high_count > 0):
            self._send_compliance_alert(
                agreement_id=str(agreement_id),
                organization_id=str(organization_id),
                score=score,
                critical=critical_count,
                high=high_count,
                total=len(violations),
            )

        return {
            "report_id": str(report.id),
            "compliance_score": score,
            "policies_checked": policies_checked,
            "violations_found": len(violations),
            "critical": critical_count,
            "high": high_count,
            "medium": medium_count,
            "low": low_count,
            "summary": report.summary,
            "violations": [
                {
                    "id": str(v.id),
                    "policy_id": str(v.policy_id),
                    "violation_type": v.violation_type,
                    "description": v.description,
                    "severity": v.severity,
                    "found_text": v.found_text,
                    "expected_text": v.expected_text,
                    "confidence": v.confidence,
                }
                for v in violations
            ],
        }

    async def _check_policy(
        self,
        policy: CompanyPolicy,
        content: str,
        content_lower: str,
    ) -> list[PolicyViolation]:
        """Check a single policy against contract content."""
        violations = []

        if policy.clause_type == "required":
            violations.extend(self._check_required(policy, content, content_lower))
        elif policy.clause_type == "prohibited":
            violations.extend(self._check_prohibited(policy, content, content_lower))
        elif policy.clause_type == "standard":
            violations.extend(self._check_standard(policy, content, content_lower))
        elif policy.clause_type in ("minimum", "maximum"):
            violations.extend(self._check_threshold(policy, content, content_lower))

        # Check rules if defined
        if policy.rules:
            violations.extend(self._check_rules(policy, content, content_lower))

        return violations

    def _check_required(
        self,
        policy: CompanyPolicy,
        content: str,
        content_lower: str,
    ) -> list[PolicyViolation]:
        """Check that a required clause is present."""
        violations = []

        if policy.keywords:
            found = any(
                kw.lower() in content_lower for kw in policy.keywords
            )
            if not found:
                violations.append(
                    PolicyViolation(
                        agreement_id=policy.id,  # Will be set by caller
                        policy_id=policy.id,
                        violation_type="missing_required",
                        description=f"Required clause '{policy.name}' not found in contract",
                        expected_text=policy.standard_text or ", ".join(policy.keywords),
                        severity=policy.severity_if_missing,
                        confidence=0.9,
                    )
                )

        return violations

    def _check_prohibited(
        self,
        policy: CompanyPolicy,
        content: str,
        content_lower: str,
    ) -> list[PolicyViolation]:
        """Check that a prohibited clause is NOT present."""
        violations = []

        if policy.keywords:
            for kw in policy.keywords:
                if kw.lower() in content_lower:
                    # Find the context around the keyword
                    idx = content_lower.index(kw.lower())
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(kw) + 50)
                    context = content[start:end]

                    violations.append(
                        PolicyViolation(
                            agreement_id=policy.id,  # Will be set by caller
                            policy_id=policy.id,
                            violation_type="prohibited_found",
                            description=f"Prohibited term '{kw}' found in contract",
                            found_text=context,
                            severity=policy.severity_if_missing,
                            confidence=0.95,
                        )
                    )

        return violations

    def _check_standard(
        self,
        policy: CompanyPolicy,
        content: str,
        content_lower: str,
    ) -> list[PolicyViolation]:
        """Check that a clause matches or is similar to the standard wording."""
        violations = []

        if policy.standard_text and policy.keywords:
            # Check if any keywords are present
            found_keywords = [
                kw for kw in policy.keywords if kw.lower() in content_lower
            ]

            if not found_keywords:
                violations.append(
                    PolicyViolation(
                        agreement_id=policy.id,  # Will be set by caller
                        policy_id=policy.id,
                        violation_type="standard_deviation",
                        description=f"Clause '{policy.name}' does not match standard wording",
                        expected_text=policy.standard_text,
                        severity=policy.severity_if_missing,
                        confidence=0.8,
                    )
                )

        return violations

    def _check_threshold(
        self,
        policy: CompanyPolicy,
        content: str,
        content_lower: str,
    ) -> list[PolicyViolation]:
        """Check numeric thresholds (min/max values)."""
        violations = []

        if not policy.rules:
            return violations

        # Extract numeric values from content
        # Match patterns like "$500,000", "USD 500000", "500,000.00"
        amount_pattern = r'(?:USD|\$|Rs\.?)\s*([\d,]+(?:\.\d+)?)'
        amounts = re.findall(amount_pattern, content, re.IGNORECASE)

        for amount_str in amounts:
            try:
                value = float(amount_str.replace(",", ""))
            except ValueError:
                continue

            # Check minimum threshold
            min_key = (
                f"min_{policy.category}"
                if policy.category
                else "min"
            )
            if min_key in policy.rules:
                try:
                    min_val = float(policy.rules[min_key])
                    if value < min_val:
                        violations.append(
                            PolicyViolation(
                                agreement_id=policy.id,  # Will be set by caller
                                policy_id=policy.id,
                                violation_type="threshold_below",
                                description=f"Value {amount_str} is below minimum {min_val} for {policy.name}",
                                found_value=amount_str,
                                expected_value=f"min: {min_val}",
                                severity=policy.severity_if_missing,
                                confidence=0.85,
                            )
                        )
                except (ValueError, TypeError):
                    pass

            # Check maximum threshold
            max_key = (
                f"max_{policy.category}"
                if policy.category
                else "max"
            )
            if max_key in policy.rules:
                try:
                    max_val = float(policy.rules[max_key])
                    if value > max_val:
                        violations.append(
                            PolicyViolation(
                                agreement_id=policy.id,  # Will be set by caller
                                policy_id=policy.id,
                                violation_type="threshold_exceeded",
                                description=f"Value {amount_str} exceeds maximum {max_val} for {policy.name}",
                                found_value=amount_str,
                                expected_value=f"max: {max_val}",
                                severity=policy.severity_if_missing,
                                confidence=0.85,
                            )
                        )
                except (ValueError, TypeError):
                    pass

        return violations

    def _check_rules(
        self,
        policy: CompanyPolicy,
        content: str,
        content_lower: str,
    ) -> list[PolicyViolation]:
        """Check structured rules defined in the policy."""
        violations = []
        rules = policy.rules

        # Check required governing law
        if "required_governing_law" in rules:
            required_laws = rules["required_governing_law"]
            if isinstance(required_laws, list):
                found_law = any(
                    law.lower() in content_lower for law in required_laws
                )
                if not found_law:
                    violations.append(
                        PolicyViolation(
                            agreement_id=policy.id,  # Will be set by caller
                            policy_id=policy.id,
                            violation_type="rule_violation",
                            description=f"Governing law must be one of: {', '.join(required_laws)}",
                            severity="high",
                            confidence=0.9,
                        )
                    )

        # Check prohibited clauses
        if "prohibited_clauses" in rules:
            prohibited = rules["prohibited_clauses"]
            if isinstance(prohibited, list):
                for clause in prohibited:
                    if clause.lower() in content_lower:
                        violations.append(
                            PolicyViolation(
                                agreement_id=policy.id,  # Will be set by caller
                                policy_id=policy.id,
                                violation_type="prohibited_found",
                                description=f"Prohibited clause found: {clause}",
                                found_text=clause,
                                severity="high",
                                confidence=0.9,
                            )
                        )

        return violations

    def _generate_summary(
        self,
        policies_checked: int,
        violations_found: int,
        critical: int,
        high: int,
        score: float,
    ) -> str:
        """Generate a human-readable compliance summary."""
        if violations_found == 0:
            return (
                f"All {policies_checked} policies checked. "
                f"No violations found. Compliance score: {score:.0f}%."
            )

        parts = [
            f"Checked {policies_checked} policies.",
            f"Found {violations_found} violation(s).",
        ]

        if critical:
            parts.append(f"{critical} CRITICAL violation(s) require immediate attention.")
        if high:
            parts.append(f"{high} HIGH severity violation(s) should be addressed.")

        parts.append(f"Compliance score: {score:.0f}%.")

        return " ".join(parts)

    def _send_compliance_alert(
        self,
        agreement_id: str,
        organization_id: str,
        score: float,
        critical: int,
        high: int,
        total: int,
    ):
        """Dispatch Slack/PagerDuty alerts for compliance violations."""
        severity = AlertSeverity.CRITICAL if critical > 0 else AlertSeverity.WARNING
        esc_level = EscalationLevel.CRITICAL if critical > 0 else EscalationLevel.WARNING

        # Send to alerting service (Slack + PagerDuty)
        alerting = get_alerting_service()
        alerting.send_alert(
            title=f"Compliance Violations Detected ({critical} critical, {high} high)",
            message=(
                f"Compliance check found {total} violation(s) "
                f"with a score of {score:.0f}%. "
                f"{critical} critical and {high} high severity violations require attention."
            ),
            severity=severity,
            source="contractos-compliance",
            category="compliance",
            details={
                "agreement_id": agreement_id,
                "compliance_score": f"{score:.0f}%",
                "critical_violations": critical,
                "high_violations": high,
                "total_violations": total,
            },
            runbook_url="https://docs.contractos.dev/runbooks/compliance-violations",
        )

        # Escalate via policy
        escalation = get_escalation_service()
        escalation.create_incident(
            organization_id=organization_id,
            category="compliance",
            title=f"Compliance violations in agreement {agreement_id[:8]}...",
            message=(
                f"Score: {score:.0f}% | "
                f"Critical: {critical} | High: {high} | Total: {total}"
            ),
            severity=esc_level,
            details={
                "agreement_id": agreement_id,
                "score": score,
                "critical": critical,
                "high": high,
            },
        )

    async def get_reports(
        self, agreement_id: UUID
    ) -> list[ComplianceReport]:
        """Get compliance reports for an agreement."""
        result = await self.db.execute(
            select(ComplianceReport)
            .where(ComplianceReport.agreement_id == agreement_id)
            .order_by(ComplianceReport.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_violations(
        self,
        agreement_id: UUID,
        severity: Optional[str] = None,
    ) -> list[PolicyViolation]:
        """Get violations for an agreement."""
        query = select(PolicyViolation).where(
            PolicyViolation.agreement_id == agreement_id
        )
        if severity:
            query = query.where(PolicyViolation.severity == severity)
        query = query.order_by(
            PolicyViolation.severity.desc(), PolicyViolation.created_at.desc()
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_violation_status(
        self,
        violation_id: UUID,
        status: str,
        notes: Optional[str] = None,
        reviewed_by: Optional[UUID] = None,
    ) -> Optional[PolicyViolation]:
        """Update the review status of a violation."""
        result = await self.db.execute(
            select(PolicyViolation).where(PolicyViolation.id == violation_id)
        )
        violation = result.scalar_one_or_none()
        if not violation:
            return None

        violation.reviewer_status = status
        violation.reviewer_notes = notes
        violation.reviewed_by = reviewed_by
        violation.reviewed_at = datetime.utcnow()

        await self.db.flush()
        return violation
