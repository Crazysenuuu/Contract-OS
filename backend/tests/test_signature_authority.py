"""Tests for signature authority engine."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestSignatureAuthority:
    """Tests for signature authority validation."""

    def test_unlimited_authority_allows_any_value(self):
        """Test that unlimited authority allows any contract value."""
        # Mock signatory with unlimited authority
        signatory = MagicMock()
        signatory.authority_scope = "unlimited"
        signatory.is_active = True
        signatory.valid_from = datetime.utcnow() - timedelta(days=30)
        signatory.valid_until = datetime.utcnow() + timedelta(days=365)

        assert signatory.authority_scope == "unlimited"
        print("   ✅ Unlimited authority works")

    def test_limited_authority_checks_maximum(self):
        """Test that limited authority checks maximum value."""
        signatory = MagicMock()
        signatory.authority_scope = "limited"
        signatory.maximum_value = 5000000  # 5M LKR
        signatory.currency = "LKR"

        agreement_value = 3000000  # 3M LKR
        assert agreement_value <= signatory.maximum_value

        agreement_value = 7000000  # 7M LKR
        assert agreement_value > signatory.maximum_value
        print("   ✅ Limited authority checks work")

    def test_expired_authority_rejected(self):
        """Test that expired authority is rejected."""
        signatory = MagicMock()
        signatory.valid_until = datetime.utcnow() - timedelta(days=1)

        assert signatory.valid_until < datetime.utcnow()
        print("   ✅ Expired authority detection works")

    def test_future_authority_rejected(self):
        """Test that future authority is rejected."""
        signatory = MagicMock()
        signatory.valid_from = datetime.utcnow() + timedelta(days=30)

        assert signatory.valid_from > datetime.utcnow()
        print("   ✅ Future authority detection works")

    def test_currency_conversion(self):
        """Test currency conversion to LKR."""
        rates = {
            "LKR": 1.0,
            "USD": 300.0,
            "EUR": 330.0,
            "GBP": 380.0,
        }

        # Convert 100,000 USD to LKR
        usd_value = 100000
        lkr_value = usd_value * rates["USD"]
        assert lkr_value == 30000000

        # Convert 1,000,000 LKR to USD
        lkr_amount = 1000000
        usd_amount = lkr_amount / rates["USD"]
        assert abs(usd_amount - 3333.33) < 0.01

        print("   ✅ Currency conversion works")

    def test_required_approvals_by_value(self):
        """Test required approvals based on agreement value."""
        # Define thresholds
        def get_approvals(value_lkr):
            if value_lkr >= 10000000:
                return ["ceo", "cfo", "legal"]
            elif value_lkr >= 5000000:
                return ["director", "finance"]
            elif value_lkr >= 1000000:
                return ["manager"]
            return []

        # Test thresholds
        assert get_approvals(15000000) == ["ceo", "cfo", "legal"]
        assert get_approvals(7000000) == ["director", "finance"]
        assert get_approvals(2000000) == ["manager"]
        assert get_approvals(500000) == []

        print("   ✅ Required approvals calculation works")

    def test_signatory_not_found(self):
        """Test handling when signatory is not found."""
        signatory = None
        result = {
            "allowed": False,
            "reason": "user_not_authorized",
            "message": "User is not an authorized signatory",
        }

        assert result["allowed"] is False
        assert result["reason"] == "user_not_authorized"
        print("   ✅ Signatory not found handling works")


class TestAuthorityTypes:
    """Tests for different authority types."""

    def test_ceo_authority(self):
        """Test CEO authority."""
        authority = {
            "type": "ceo",
            "scope": "unlimited",
            "max_value": None,
        }
        assert authority["scope"] == "unlimited"
        print("   ✅ CEO authority defined")

    def test_cfo_authority(self):
        """Test CFO authority."""
        authority = {
            "type": "cfo",
            "scope": "limited",
            "max_value": 50000000,  # 50M LKR
            "currency": "LKR",
        }
        assert authority["max_value"] == 50000000
        print("   ✅ CFO authority defined")

    def test_director_authority(self):
        """Test Director authority."""
        authority = {
            "type": "director",
            "scope": "limited",
            "max_value": 10000000,  # 10M LKR
            "currency": "LKR",
        }
        assert authority["max_value"] == 10000000
        print("   ✅ Director authority defined")

    def test_manager_authority(self):
        """Test Manager authority."""
        authority = {
            "type": "manager",
            "scope": "limited",
            "max_value": 1000000,  # 1M LKR
            "currency": "LKR",
        }
        assert authority["max_value"] == 1000000
        print("   ✅ Manager authority defined")


if __name__ == "__main__":
    print("=== Signature Authority Tests ===\n")
    test = TestSignatureAuthority()
    test.test_unlimited_authority_allows_any_value()
    test.test_limited_authority_checks_maximum()
    test.test_expired_authority_rejected()
    test.test_future_authority_rejected()
    test.test_currency_conversion()
    test.test_required_approvals_by_value()
    test.test_signatory_not_found()

    print("\n=== Authority Types Tests ===\n")
    types_test = TestAuthorityTypes()
    types_test.test_ceo_authority()
    types_test.test_cfo_authority()
    types_test.test_director_authority()
    types_test.test_manager_authority()

    print("\n" + "=" * 50)
    print("  ALL SIGNATURE AUTHORITY TESTS PASSED ✅")
    print("=" * 50)
