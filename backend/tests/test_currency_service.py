"""Spec §71 — no hardcoded business data: currency comes from configuration."""

import pytest

from app.core.config import get_settings_lazy
from app.services import currency_service as cs


@pytest.fixture(autouse=True)
def _reset_rates():
    cs.reset_cache()
    yield
    cs.reset_cache()


def test_default_currency_comes_from_settings():
    assert cs.default_currency() == get_settings_lazy().default_currency.upper()


def test_resolve_currency_prefers_most_specific_non_empty():
    assert cs.resolve_currency(None, "", "usd") == "USD"
    assert cs.resolve_currency(None, None) == cs.default_currency()


def test_convert_round_trips_through_base():
    rates = cs.fx_rates()
    assert rates[cs.default_currency()] == 1.0
    usd_in_base = cs.convert(10, "USD", cs.default_currency())
    assert usd_in_base == pytest.approx(10 * rates["USD"])
    assert cs.convert(usd_in_base, cs.default_currency(), "USD") == pytest.approx(10)


def test_unknown_currency_is_flagged_not_crashing():
    assert cs.is_known_currency("XXX") is False
    assert cs.convert(5, "XXX", cs.default_currency()) == 5  # treated 1:1, caller may warn


def test_rates_are_configurable(monkeypatch):
    settings = get_settings_lazy()
    monkeypatch.setattr(settings, "fx_rates_json", '{"USD": 2.0}')
    monkeypatch.setattr(settings, "default_currency", "EUR")
    cs.reset_cache()
    assert cs.default_currency() == "EUR"
    assert cs.fx_rates() == {"USD": 2.0, "EUR": 1.0}
    assert cs.convert_to_base(3, "USD") == 6.0
