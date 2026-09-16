"""Company policy engine for contract governance rules."""
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
import json

from app.models.agreement import Agreement
from app.models.legal_entity import LegalEntity


def _cond_value_above(ctx: Dict[str, Any], threshold: float = 0.0) -> bool:
    return ctx.get("value_lkr", 0) > threshold


def _cond_term_above(ctx: Dict[str, Any], threshold: float = 0.0) -> bool:
    return ctx.get("term_years", 0) > threshold


def _cond_unlimited_liability(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("has_unlimited_liability", False))


def _cond_personal_data(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("involves_personal_data", False))


def _cond_foreign_jurisdiction(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("is_foreign_jurisdiction", False))


def _cond_exclusive(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("is_exclusive", False))


def _cond_ip_assignment(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("involves_ip_assignment", False))


def _cond_missing_termination(ctx: Dict[str, Any]) -> bool:
    return not bool(ctx.get("has_termination_clause", True))


def _make_threshold(context_key: str, threshold: float):
    """Build a one-shot threshold predicate for a numeric context key."""

    def _predicate(ctx: Dict[str, Any]) -> bool:
        return ctx.get(context_key, 0) > threshold

    return _predicate


# Named condition predicates shared by default rules and add_custom_rule.
_CONDITION_PREDICATES = {
    "value_above": _cond_value_above,
    "term_above": _cond_term_above,
    "has_unlimited_liability": _cond_unlimited_liability,
    "has_personal_data": _cond_personal_data,
    "involves_personal_data": _cond_personal_data,
    "is_foreign": _cond_foreign_jurisdiction,
    "is_foreign_jurisdiction": _cond_foreign_jurisdiction,
    "is_exclusive": _cond_exclusive,
    "involves_ip_assignment": _cond_ip_assignment,
    "missing_termination": _cond_missing_termination,
}


class CompanyPolicyEngine:
    """Engine for evaluating company policies against agreements."""

    def __init__(self, db: Session):
        self.db = db
        self._rules: List[Dict] = []
        self._load_default_rules()

    def _load_default_rules(self):
        """Load default company policy rules."""
        self._rules = [
            {
                "id": "high_value_approval",
                "name": "High Value Approval",
                "description": "Agreements over 10M LKR require executive approval",
                "condition": _make_threshold("value_lkr", 10000000),
                "action": "require_approval",
                "approval_roles": ["ceo", "cfo", "legal"],
                "severity": "critical",
            },
            {
                "id": "unlimited_liability",
                "name": "Unlimited Liability Review",
                "description": "Agreements with unlimited liability require legal review",
                "condition": _CONDITION_PREDICATES["has_unlimited_liability"],
                "action": "require_approval",
                "approval_roles": ["legal"],
                "severity": "high",
            },
            {
                "id": "long_term_contract",
                "name": "Long Term Contract Review",
                "description": "Agreements over 5 years require director approval",
                "condition": _make_threshold("term_years", 5),
                "action": "require_approval",
                "approval_roles": ["director"],
                "severity": "medium",
            },
            {
                "id": "personal_data_processing",
                "name": "Personal Data Processing",
                "description": "Agreements involving personal data require privacy review",
                "condition": _CONDITION_PREDICATES["has_personal_data"],
                "action": "require_approval",
                "approval_roles": ["privacy_officer"],
                "severity": "high",
            },
            {
                "id": "foreign_jurisdiction",
                "name": "Foreign Jurisdiction Review",
                "description": "Agreements with foreign governing law require legal review",
                "condition": _CONDITION_PREDICATES["is_foreign_jurisdiction"],
                "action": "require_approval",
                "approval_roles": ["legal"],
                "severity": "medium",
            },
            {
                "id": "exclusive_relationship",
                "name": "Exclusive Relationship Review",
                "description": "Exclusive agreements require director approval",
                "condition": _CONDITION_PREDICATES["is_exclusive"],
                "action": "require_approval",
                "approval_roles": ["director"],
                "severity": "medium",
            },
            {
                "id": "ip_assignment",
                "name": "IP Assignment Review",
                "description": "IP assignment agreements require legal review",
                "condition": _CONDITION_PREDICATES["involves_ip_assignment"],
                "action": "require_approval",
                "approval_roles": ["legal"],
                "severity": "high",
            },
            {
                "id": "termination_terms",
                "name": "Termination Terms Review",
                "description": "Agreements without termination clause require legal review",
                "condition": _CONDITION_PREDICATES["missing_termination"],
                "action": "flag_warning",
                "severity": "medium",
            },
        ]

    def evaluate_agreement(
        self,
        agreement: Agreement,
        agreement_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Evaluate an agreement against company policies."""
        if agreement_data is None:
            agreement_data = {}

        # Build context
        context = self._build_context(agreement, agreement_data)

        # Evaluate rules
        triggered_rules = []
        warnings = []
        required_approvals = []

        for rule in self._rules:
            try:
                condition = rule.get("condition")
                if condition is None or not condition(context):
                    continue
                    if rule["action"] == "require_approval":
                        triggered_rules.append(rule)
                        for role in rule.get("approval_roles", []):
                            if role not in [a["role"] for a in required_approvals]:
                                required_approvals.append({
                                    "role": role,
                                    "rule_id": rule["id"],
                                    "rule_name": rule["name"],
                                    "severity": rule["severity"],
                                })
                    elif rule["action"] == "flag_warning":
                        warnings.append({
                            "rule_id": rule["id"],
                            "rule_name": rule["name"],
                            "description": rule["description"],
                            "severity": rule["severity"],
                        })
            except Exception:
                continue

        # Calculate compliance score
        total_rules = len(self._rules)
        triggered_count = len(triggered_rules)
        compliance_score = max(0, 100 - (triggered_count * 15) - (len(warnings) * 5))

        return {
            "compliance_score": compliance_score,
            "rules_evaluated": total_rules,
            "rules_triggered": triggered_count,
            "warnings": warnings,
            "required_approvals": required_approvals,
            "context": {k: v for k, v in context.items() if not k.startswith("_")},
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _build_context(
        self,
        agreement: Agreement,
        agreement_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build evaluation context from agreement."""
        context = {
            "agreement_id": str(agreement.id),
            "status": agreement.status,
        }

        # Extract from agreement data
        data = agreement.data or {}
        data.update(agreement_data)

        # Value
        if "total_value" in data:
            try:
                value = float(data["total_value"])
                currency = data.get("currency", "LKR")
                context["value_lkr"] = self._convert_to_lkr(value, currency)
            except (ValueError, TypeError):
                pass

        # Term
        if "term_years" in data:
            try:
                context["term_years"] = float(data["term_years"])
            except (ValueError, TypeError):
                pass

        # Liability
        if "liability_cap" in data:
            cap = str(data["liability_cap"]).lower()
            context["has_unlimited_liability"] = cap in ["unlimited", "none", "no limit"]

        # Personal data
        if "involves_personal_data" in data:
            context["involves_personal_data"] = bool(data["involves_personal_data"])

        # Jurisdiction
        governing_law = agreement.governing_law or data.get("governing_law", "")
        if governing_law:
            domestic_jurisdictions = ["sri lanka", "lk", "lka"]
            context["is_foreign_jurisdiction"] = governing_law.lower() not in domestic_jurisdictions

        # Exclusivity
        if "is_exclusive" in data:
            context["is_exclusive"] = bool(data["is_exclusive"])

        # IP
        if "involves_ip_assignment" in data:
            context["involves_ip_assignment"] = bool(data["involves_ip_assignment"])

        # Termination
        if "has_termination_clause" in data:
            context["has_termination_clause"] = bool(data["has_termination_clause"])

        return context

    def _convert_to_lkr(self, amount: float, currency: str) -> float:
        """Convert amount to LKR."""
        rates = {
            "LKR": 1.0,
            "USD": 300.0,
            "EUR": 330.0,
            "GBP": 380.0,
            "SGD": 225.0,
            "INR": 3.6,
        }
        return amount * rates.get(currency, 1.0)

    def get_all_rules(self) -> List[Dict]:
        """Get all policy rules."""
        return [
            {
                "id": rule["id"],
                "name": rule["name"],
                "description": rule["description"],
                "action": rule["action"],
                "approval_roles": rule.get("approval_roles", []),
                "severity": rule["severity"],
            }
            for rule in self._rules
        ]

    def add_custom_rule(
        self,
        rule_id: str,
        name: str,
        description: str,
        condition_key: str,
        action: str = "require_approval",
        approval_roles: List[str] = None,
        severity: str = "medium"
    ):
        """Add a custom policy rule.

        ``condition_key`` selects a registered condition predicate by name
        (optionally ``"name:threshold"``); unknown keys are stored but never
        fire, so a typo cannot silently approve everything.
        """
        key, _, raw_threshold = condition_key.partition(":")
        threshold: Any = None
        if raw_threshold:
            try:
                threshold = float(raw_threshold)
            except ValueError:
                threshold = raw_threshold

        condition = _CONDITION_PREDICATES.get(key)
        if condition is not None and key in ("value_above", "term_above"):
            bound_threshold = threshold
            rule_condition = (
                lambda ctx, _cond=condition, _t=bound_threshold: _cond(ctx, _t)
            )
        else:
            rule_condition = condition

        rule = {
            "id": rule_id,
            "name": name,
            "description": description,
            "condition": rule_condition,
            "action": action,
            "approval_roles": approval_roles or [],
            "severity": severity,
            "condition_key": condition_key,
        }
        self._rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a policy rule."""
        original_count = len(self._rules)
        self._rules = [r for r in self._rules if r["id"] != rule_id]
        return len(self._rules) < original_count

    def get_policy_summary(self) -> Dict[str, Any]:
        """Get summary of company policies."""
        return {
            "total_rules": len(self._rules),
            "rules_by_severity": {
                "critical": len([r for r in self._rules if r["severity"] == "critical"]),
                "high": len([r for r in self._rules if r["severity"] == "high"]),
                "medium": len([r for r in self._rules if r["severity"] == "medium"]),
                "low": len([r for r in self._rules if r["severity"] == "low"]),
            },
            "approval_roles_required": list(set(
                role for rule in self._rules
                for role in rule.get("approval_roles", [])
            )),
        }
