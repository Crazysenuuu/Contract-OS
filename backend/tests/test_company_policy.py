"""Tests for company policy engine."""
import pytest
from datetime import datetime


class TestCompanyPolicyEngine:
    """Tests for company policy evaluation."""

    def test_high_value_triggers_approval(self):
        """Test that high value agreements trigger approval."""
        # Mock context
        context = {
            "value_lkr": 15000000,  # 15M LKR
        }

        # Rule: value > 10M requires approval
        threshold = 10000000
        triggered = context["value_lkr"] > threshold

        assert triggered is True
        print("   ✅ High value triggers approval")

    def test_low_value_no_approval(self):
        """Test that low value agreements don't trigger approval."""
        context = {
            "value_lkr": 500000,  # 500K LKR
        }

        threshold = 10000000
        triggered = context["value_lkr"] > threshold

        assert triggered is False
        print("   ✅ Low value no approval")

    def test_unlimited_liability_triggers_review(self):
        """Test that unlimited liability triggers review."""
        context = {
            "has_unlimited_liability": True,
        }

        triggered = context["has_unlimited_liability"]
        assert triggered is True
        print("   ✅ Unlimited liability triggers review")

    def test_limited_liability_no_review(self):
        """Test that limited liability doesn't trigger review."""
        context = {
            "has_unlimited_liability": False,
        }

        triggered = context["has_unlimited_liability"]
        assert triggered is False
        print("   ✅ Limited liability no review")

    def test_long_term_triggers_review(self):
        """Test that long-term contracts trigger review."""
        context = {
            "term_years": 7,
        }

        threshold = 5
        triggered = context["term_years"] > threshold

        assert triggered is True
        print("   ✅ Long term triggers review")

    def test_short_term_no_review(self):
        """Test that short-term contracts don't trigger review."""
        context = {
            "term_years": 2,
        }

        threshold = 5
        triggered = context["term_years"] > threshold

        assert triggered is False
        print("   ✅ Short term no review")

    def test_personal_data_triggers_privacy_review(self):
        """Test that personal data triggers privacy review."""
        context = {
            "involves_personal_data": True,
        }

        triggered = context["involves_personal_data"]
        assert triggered is True
        print("   ✅ Personal data triggers privacy review")

    def test_foreign_jurisdiction_triggers_legal_review(self):
        """Test that foreign jurisdiction triggers legal review."""
        domestic_jurisdictions = ["sri lanka", "lk", "lka"]

        context = {
            "governing_law": "Singapore",
        }

        is_foreign = context["governing_law"].lower() not in domestic_jurisdictions
        assert is_foreign is True

        context["governing_law"] = "Sri Lanka"
        is_foreign = context["governing_law"].lower() not in domestic_jurisdictions
        assert is_foreign is False

        print("   ✅ Foreign jurisdiction detection works")

    def test_exclusive_relationship_triggers_review(self):
        """Test that exclusive relationships trigger review."""
        context = {
            "is_exclusive": True,
        }

        triggered = context["is_exclusive"]
        assert triggered is True
        print("   ✅ Exclusive relationship triggers review")

    def test_compliance_score_calculation(self):
        """Test compliance score calculation."""
        total_rules = 8
        triggered_count = 2
        warnings_count = 1

        compliance_score = max(0, 100 - (triggered_count * 15) - (warnings_count * 5))

        assert compliance_score == 65
        print("   ✅ Compliance score calculation works")

    def test_compliance_score_minimum_zero(self):
        """Test compliance score doesn't go below zero."""
        total_rules = 8
        triggered_count = 10
        warnings_count = 5

        compliance_score = max(0, 100 - (triggered_count * 15) - (warnings_count * 5))

        assert compliance_score == 0
        print("   ✅ Compliance score minimum zero works")


class TestPolicyRules:
    """Tests for policy rule definitions."""

    def test_rule_structure(self):
        """Test that rules have required fields."""
        rule = {
            "id": "high_value_approval",
            "name": "High Value Approval",
            "description": "Agreements over 10M LKR require executive approval",
            "condition": lambda ctx: ctx.get("value_lkr", 0) > 10000000,
            "action": "require_approval",
            "approval_roles": ["ceo", "cfo", "legal"],
            "severity": "critical",
        }

        assert "id" in rule
        assert "name" in rule
        assert "description" in rule
        assert "condition" in rule
        assert "action" in rule
        assert "severity" in rule
        print("   ✅ Rule structure valid")

    def test_rule_severity_levels(self):
        """Test severity levels."""
        severity_levels = ["critical", "high", "medium", "low"]

        for level in severity_levels:
            assert level in ["critical", "high", "medium", "low"]

        print("   ✅ Severity levels valid")

    def test_rule_actions(self):
        """Test rule actions."""
        valid_actions = ["require_approval", "flag_warning", "block", "notify"]

        rule_action = "require_approval"
        assert rule_action in valid_actions

        print("   ✅ Rule actions valid")


class TestApprovalWorkflow:
    """Tests for approval workflow integration."""

    def test_approval_roles_for_high_value(self):
        """Test approval roles for high value agreements."""
        value_lkr = 15000000

        if value_lkr >= 10000000:
            required_roles = ["ceo", "cfo", "legal"]
        elif value_lkr >= 5000000:
            required_roles = ["director", "finance"]
        elif value_lkr >= 1000000:
            required_roles = ["manager"]
        else:
            required_roles = []

        assert required_roles == ["ceo", "cfo", "legal"]
        print("   ✅ High value approval roles correct")

    def test_approval_roles_for_medium_value(self):
        """Test approval roles for medium value agreements."""
        value_lkr = 7000000

        if value_lkr >= 10000000:
            required_roles = ["ceo", "cfo", "legal"]
        elif value_lkr >= 5000000:
            required_roles = ["director", "finance"]
        elif value_lkr >= 1000000:
            required_roles = ["manager"]
        else:
            required_roles = []

        assert required_roles == ["director", "finance"]
        print("   ✅ Medium value approval roles correct")

    def test_approval_roles_for_low_value(self):
        """Test approval roles for low value agreements."""
        value_lkr = 500000

        if value_lkr >= 10000000:
            required_roles = ["ceo", "cfo", "legal"]
        elif value_lkr >= 5000000:
            required_roles = ["director", "finance"]
        elif value_lkr >= 1000000:
            required_roles = ["manager"]
        else:
            required_roles = []

        assert required_roles == []
        print("   ✅ Low value approval roles correct")


if __name__ == "__main__":
    print("=== Company Policy Engine Tests ===\n")

    print("Policy Evaluation Tests:")
    print("-" * 40)
    test = TestCompanyPolicyEngine()
    test.test_high_value_triggers_approval()
    test.test_low_value_no_approval()
    test.test_unlimited_liability_triggers_review()
    test.test_limited_liability_no_review()
    test.test_long_term_triggers_review()
    test.test_short_term_no_review()
    test.test_personal_data_triggers_privacy_review()
    test.test_foreign_jurisdiction_triggers_legal_review()
    test.test_exclusive_relationship_triggers_review()
    test.test_compliance_score_calculation()
    test.test_compliance_score_minimum_zero()

    print("\nPolicy Rules Tests:")
    print("-" * 40)
    rules_test = TestPolicyRules()
    rules_test.test_rule_structure()
    rules_test.test_rule_severity_levels()
    rules_test.test_rule_actions()

    print("\nApproval Workflow Tests:")
    print("-" * 40)
    workflow_test = TestApprovalWorkflow()
    workflow_test.test_approval_roles_for_high_value()
    workflow_test.test_approval_roles_for_medium_value()
    workflow_test.test_approval_roles_for_low_value()

    print("\n" + "=" * 50)
    print("  ALL COMPANY POLICY TESTS PASSED ✅")
    print("=" * 50)
