"""Fail-fast production configuration validation.

Every setting that silently degrades in production is a launch-day outage
waiting to happen: a default JWT secret means anyone can mint a token for
any user; an unconfigured SendGrid key means verification and signing
invites are dropped without an error; the mock e-signature provider means
contracts are "signed" by nobody. None of these fail loudly, so they ship.

This module runs once at application startup and refuses to boot when a
production deployment is missing a credential it cannot run without.
Warnings (things that are wrong but survivable) are logged instead of
raising so a misconfigured staging environment stays diagnosable.

Escape hatch: ``ALLOW_INSECURE_CONFIG=true`` downgrades every error to a
warning. It exists for single-box deployments that terminate TLS outside
the app and for CI images that intentionally run the mock providers; it
must never be set in a real production environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings_lazy

logger = logging.getLogger(__name__)

#: Environments where the strict checks apply. Staging is deliberately
#: excluded: staging is allowed to run the mock e-signature/OCR providers so
#: end-to-end rehearsals work without burning production API quotas.
STRICT_ENVIRONMENTS = frozenset({"production"})

#: Settings whose *default* value is a well-known constant. If the effective
#: value still equals the default in production, the secret is public.
_PLACEHOLDER_SECRETS: tuple[tuple[str, str], ...] = (
    ("jwt_secret_key", "dev-secret-change-me-in-production"),
    ("email_opt_out_token", "change-me-opt-out-secret"),
)


@dataclass
class ValidationReport:
    """Outcome of a configuration validation pass."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_on_errors(self) -> None:
        if not self.errors:
            return
        bullets = "\n".join(f"  - {e}" for e in self.errors)
        raise RuntimeError(
            "Refusing to start: the configuration is not production-safe.\n"
            f"{bullets}\n\n"
            "Set the missing values in the environment (see backend/.env.example), "
            "or set ALLOW_INSECURE_CONFIG=true to boot anyway — the latter is not "
            "safe for a real deployment."
        )


def _secret_value(settings: Settings, field_name: str) -> str | None:
    raw = getattr(settings, field_name, None)
    if raw is None:
        return None
    getter = getattr(raw, "get_secret_value", None)
    return getter() if callable(getter) else str(raw)


def _validate_secrets(settings: Settings, report: ValidationReport) -> None:
    """Placeholder or absent secrets that must never reach production."""
    for name, placeholder in _PLACEHOLDER_SECRETS:
        value = _secret_value(settings, name)
        if value is None:
            report.errors.append(f"{name} is not set")
        elif value.strip() == placeholder:
            report.errors.append(f"{name} still uses its documented placeholder")
        elif len(value.strip()) < 32:
            report.errors.append(
                f"{name} is too short ({len(value.strip())} chars); use 32+ random chars"
            )

    # The field-encryption key protects PII at rest. Falling back to a key
    # derived from the JWT secret is doubly wrong: it is unsettable in
    # Settings (read straight from the environment) and it makes a JWT
    # rotation permanently unreadable ciphertext.
    master_key = os.environ.get("FIELD_ENCRYPTION_MASTER_KEY")
    if not master_key:
        report.errors.append(
            "FIELD_ENCRYPTION_MASTER_KEY is not set (PII at rest would fall back "
            "to a key derived from JWT_SECRET_KEY, and a JWT rotation would "
            "destroy the encrypted data)"
        )
    elif len(master_key.strip()) < 32:
        report.errors.append("FIELD_ENCRYPTION_MASTER_KEY is too short; use 32+ chars")

    document_token_secret = os.environ.get("DOCUMENT_TOKEN_SECRET")
    if not document_token_secret:
        report.warnings.append(
            "DOCUMENT_TOKEN_SECRET is not set; document download links will be "
            "signed with JWT_SECRET_KEY, coupling two independent secrets"
        )
    elif document_token_secret.strip() in (
        "change-me-generate-a-long-random-secret",
    ):
        report.errors.append("DOCUMENT_TOKEN_SECRET still uses its placeholder")


def _validate_endpoints(settings: Settings, report: ValidationReport) -> None:
    """Addresses and URLs that silently point back at the developer's laptop."""
    base = (settings.app_base_url or "").strip()
    if not base:
        report.errors.append("APP_BASE_URL is not set")
    else:
        if "localhost" in base or "127.0.0.1" in base:
            report.errors.append(
                f"APP_BASE_URL is {base!r}; verification and signing links would "
                "point at localhost and no recipient could ever open them"
            )
        if not base.startswith("https://"):
            report.errors.append(f"APP_BASE_URL ({base}) must use https")

    origins = [o.strip() for o in (settings.cors_origins or "").split(",") if o.strip()]
    if not origins:
        report.errors.append("CORS_ORIGINS is empty; every browser request will be blocked")
    for origin in origins:
        if origin == "*":
            report.errors.append(
                "CORS_ORIGINS contains '*', which is invalid with credentials "
                "and would allow any site to call the API with the user's cookie"
            )
        elif "localhost" in origin or "127.0.0.1" in origin:
            report.errors.append(f"CORS_ORIGINS still allows the dev origin {origin!r}")

    if settings.email_postal_address.strip().lower().startswith("contractos (pvt)"):
        # The shipped default is a placeholder legal entity, not the operator's.
        report.warnings.append(
            "EMAIL_POSTAL_ADDRESS is still the sample value; CAN-SPAM requires "
            "the real sender's postal address in every outbound footer"
        )


def _validate_delivery(settings: Settings, report: ValidationReport) -> None:
    """Mail, push and SMS: the channels that fail silently when unset."""
    if not settings.sendgrid_api_key:
        report.errors.append(
            "SENDGRID_API_KEY is not set; verification emails, signing "
            "invitations, approvals and reminders would be silently dropped"
        )
    if "@" not in settings.email_from_address:
        report.errors.append(f"EMAIL_FROM_ADDRESS ({settings.email_from_address!r}) is not an address")


def _validate_providers(settings: Settings, report: ValidationReport) -> None:
    """Stub providers must not reach production."""
    if settings.esignature_provider in ("mock", "", None):
        report.errors.append(
            f"ESIGNATURE_PROVIDER is {settings.esignature_provider!r}; contracts "
            "would be 'signed' without a legally binding signature. Set "
            "'docusign' or 'adobe_sign'."
        )
    if settings.kyc_provider in ("mock", "", None):
        report.errors.append(
            f"KYC_PROVIDER is {settings.kyc_provider!r}; identity verification "
            "for external signers would be simulated"
        )
    if settings.embedding_provider == "hash":
        report.errors.append(
            "EMBEDDING_PROVIDER is 'hash'; contract-intelligence retrieval uses "
            "deterministic local vectors and will not find semantically related "
            "clauses. Set 'openai'."
        )
    if settings.ocr_provider == "mock":
        report.errors.append("OCR_PROVIDER is 'mock'; document text extraction is simulated")


def _validate_storage(settings: Settings, report: ValidationReport) -> None:
    """Local-disk storage loses every uploaded contract on redeploy."""
    if settings.storage_backend not in ("s3", "local"):
        report.errors.append(f"STORAGE_BACKEND ({settings.storage_backend!r}) must be 's3' or 'local'")
        return
    if settings.storage_backend == "local":
        report.errors.append(
            "STORAGE_BACKEND is 'local'; documents and backups live on the "
            "container filesystem and are lost on every redeploy or when a new "
            "replica starts. Set STORAGE_BACKEND=s3."
        )
    else:
        if not settings.s3_access_key_id or not settings.s3_secret_access_key:
            report.errors.append("STORAGE_BACKEND=s3 requires S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
        if not settings.s3_bucket:
            report.errors.append("STORAGE_BACKEND=s3 requires S3_BUCKET")


def _validate_background_work(settings: Settings, report: ValidationReport) -> None:
    """Celery drives reminders, SLA sweeps and outbox delivery."""
    if not settings.redis_url:
        report.errors.append(
            "REDIS_URL is not set; Celery has no broker, so obligation "
            "reminders, SLA sweeps, expiry notifications and outbox delivery "
            "never run"
        )


def _validate_exposure(settings: Settings, report: ValidationReport) -> None:
    """Introspection surfaces that must not be world-readable in production."""
    if getattr(settings, "expose_api_docs", False):
        # An error, not a warning: /docs enumerates every route, schema and
        # dependency, which is a complete attack-surface map. There is no
        # legitimate reason for a live deployment to serve it publicly, and
        # ALLOW_INSECURE_CONFIG remains the documented escape hatch.
        report.errors.append(
            "EXPOSE_API_DOCS is enabled in production; /docs publishes the full "
            "API surface. Set EXPOSE_API_DOCS=false (or use ALLOW_INSECURE_CONFIG "
            "to bypass deliberately)."
        )


def validate_configuration(settings: Settings | None = None) -> ValidationReport:
    """Run every applicable check and return a report.

    Non-strict environments (development, test, staging) get warnings for the
    same conditions so they surface in logs during rehearsals without
    blocking a boot.
    """
    settings = settings or get_settings_lazy()
    report = ValidationReport()
    strict = settings.environment in STRICT_ENVIRONMENTS
    allow_insecure = os.environ.get("ALLOW_INSECURE_CONFIG", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    for check in (
        _validate_secrets,
        _validate_endpoints,
        _validate_delivery,
        _validate_providers,
        _validate_storage,
        _validate_background_work,
        _validate_exposure,
    ):
        check(settings, report)

    if not strict:
        # Surface the same findings without failing the boot, but keep the
        # ones that only matter in production out of a developer's face.
        report.warnings.extend(f"[{settings.environment}] {e}" for e in report.errors)
        report.errors = []
        return report

    if allow_insecure:
        for error in report.errors:
            logger.error(
                "INSECURE CONFIG (ALLOW_INSECURE_CONFIG=true): %s", error
            )
        report.warnings.append(
            "ALLOW_INSECURE_CONFIG=true bypassed "
            f"{len(report.errors)} production safety check(s)"
        )
        report.errors = []
        return report

    return report


def validate_or_die(settings: Settings | None = None) -> ValidationReport:
    """Validate the configuration and refuse to boot on production errors."""
    report = validate_configuration(settings)
    for warning in report.warnings:
        logger.warning("startup config: %s", warning)
    if report.errors:
        logger.error("startup config: %d blocking problem(s) found", len(report.errors))
    report.raise_on_errors()
    if report.warnings:
        logger.info("startup config: %d warning(s)", len(report.warnings))
    return report
