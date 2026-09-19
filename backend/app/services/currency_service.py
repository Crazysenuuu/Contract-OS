"""Currency configuration (spec §71 — no hardcoded business data).

The platform's base currency and the (simplified) FX table used for
signing-authority, delegation-of-authority and policy thresholds used to be
copy-pasted as ``{"LKR": 1.0, "USD": 300.0, ...}`` in three services. They
now come from configuration:

* ``DEFAULT_CURRENCY``  — ISO-4217 base currency for thresholds (default LKR
  because the launch market is Sri Lanka, but it is *configuration*).
* ``FX_RATES_JSON``     — JSON object mapping currency → units of the base
  currency per 1 unit of that currency, e.g. ``{"USD": 300.0}``.

Production deployments should feed ``FX_RATES_JSON`` from a rates provider;
this module is deliberately the only place that knows the shape.
"""

from __future__ import annotations

import json
from functools import lru_cache

from app.core.config import get_settings_lazy


@lru_cache(maxsize=1)
def _rates() -> dict[str, float]:
    settings = get_settings_lazy()
    raw = settings.fx_rates_json or "{}"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    rates = {str(k).upper(): float(v) for k, v in parsed.items() if v}
    rates.setdefault(default_currency(), 1.0)
    return rates


def default_currency() -> str:
    """The organisation-agnostic platform base currency (ISO-4217)."""
    return (get_settings_lazy().default_currency or "LKR").upper()


def resolve_currency(*candidates: str | None) -> str:
    """First non-empty currency code among ``candidates``, else the default.

    Callers pass the most specific source first, e.g.
    ``resolve_currency(request.currency, agreement.currency, org_currency)``.
    """
    for c in candidates:
        if c and str(c).strip():
            return str(c).strip().upper()
    return default_currency()


def fx_rates() -> dict[str, float]:
    return dict(_rates())


def convert(amount: float, from_currency: str | None, to_currency: str | None = None) -> float:
    """Convert ``amount`` between currencies using the configured table.

    Unknown currencies are treated as 1:1 with the base currency (and this
    is surfaced by :func:`is_known_currency` so callers can warn).
    """
    rates = _rates()
    src = resolve_currency(from_currency)
    dst = resolve_currency(to_currency)
    base_amount = amount * rates.get(src, 1.0)
    return base_amount / rates.get(dst, 1.0)


def convert_to_base(amount: float, currency: str | None) -> float:
    return convert(amount, currency, default_currency())


def is_known_currency(currency: str | None) -> bool:
    return resolve_currency(currency) in _rates()


def reset_cache() -> None:
    """Test hook: drop the memoised rate table after changing settings."""
    _rates.cache_clear()
