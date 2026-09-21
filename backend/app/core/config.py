from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ContractOS"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://user:pass@localhost/contractos"

    jwt_secret_key: SecretStr = SecretStr("dev-secret-change-me-in-production")
    jwt_algorithm: str = "HS256"

    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    cors_origins: str = "http://localhost:3000"

    # Email / SendGrid
    sendgrid_api_key: SecretStr | None = None
    email_from_address: str = "noreply@contractos.lk"
    email_from_name: str = "ContractOS"
    app_base_url: str = "http://localhost:3000"

    # Alerting — Slack
    slack_webhook_url: str | None = None
    slack_channel: str | None = None

    # Alerting — PagerDuty
    pagerduty_routing_key: SecretStr | None = None
    pagerduty_from_email: str = "contractos@alerts.local"

    # Alerting — general
    alerting_enabled: bool = True

    # Distributed tracing / OpenTelemetry (spec 23 / 1.23). Disabled by
    # default so local dev stays dependency-free; enable with TRACING_ENABLED
    # and point TRACING_OTLP_ENDPOINT at a collector.
    tracing_enabled: bool = False
    tracing_service_name: str = "contractos-backend"
    tracing_otlp_endpoint: str | None = None  # e.g. http://collector:4318/v1/traces
    tracing_sample_ratio: float = 1.0

    # Backup / disaster recovery (spec 39-40 / 58). Encrypted pg_dump backups
    # are written under backup_dir and pruned to backup_retention_count.
    backup_enabled: bool = True
    backup_dir: str = "storage/backups"
    backup_retention_count: int = 14

    # Document object storage (spec 1.23 / 2.07)
    # storage_backend: 'local' (filesystem under document_storage_dir) or
    # 's3' (S3-compatible object storage: AWS S3 or MinIO via endpoint_url).
    document_storage_dir: str = "storage/documents"
    storage_backend: str = "local"

    # S3/MinIO object-storage configuration (used when storage_backend == 's3').
    s3_bucket: str = "contractos-documents"
    s3_endpoint_url: str | None = None  # set for MinIO; None uses AWS default
    s3_region_name: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    # Enterprise SSO / OIDC (spec 1.21.8 / 2.15)
    oidc_state_ttl_seconds: int = 600
    oidc_http_timeout_seconds: float = 15.0

    # OCR pipeline (spec 24.1). Implicit default is AWS Textract with a
    # Google Document AI fallback (chosen over the mock provider by product
    # decision); the mock engine remains available for dev/tests ONLY via
    # explicit selection and is refused outright in production.
    ocr_provider: str = ""  # '' -> implicit default chain (aws -> google)
    ocr_fallback_provider: str = "google_document_ai"
    ocr_confidence_threshold: float = 0.8

    # E-signature provider: 'mock' | 'docusign' | 'adobe_sign' (spec 24.5)
    esignature_provider: str = "mock"
    esign_callback_base_url: str | None = None

    # DocuSign credentials (used when esignature_provider == 'docusign')
    docusign_integration_key: SecretStr | None = None
    docusign_user_id: SecretStr | None = None
    docusign_account_id: SecretStr | None = None
    docusign_private_key_path: str = "keys/docusign_private.pem"
    docusign_oauth_base_url: str = "account-d.docusign.com"
    docusign_base_path: str = "https://demo.docusign.net/restapi"

    # Adobe Acrobat Sign credentials (used when esignature_provider == 'adobe_sign')
    adobesign_access_token: SecretStr | None = None
    adobesign_base_url: str = "https://api.echosign.com/api/rest/v6"
    adobesign_library_id: str | None = None

    # Inbound provider webhooks (spec 24.5): HMAC-SHA256 shared secrets for
    # DocuSign Connect / Adobe Sign callbacks. When unset the endpoint
    # refuses to process events (503), mirroring the billing webhook.
    docusign_webhook_secret: SecretStr | None = None
    adobe_webhook_secret: SecretStr | None = None

    # AI provider credentials (spec 17/18/36). Without a key the AI service
    # degrades to its rule-based fallback extractor.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o"
    anthropic_api_key: SecretStr | None = None
    ai_provider: str = "openai"

    # Long-contract analysis (spec 1.19.24). Long contracts are split into
    # overlapping chunks so the model reads the whole agreement instead of
    # silently analyzing only the first N chars. 0 disables chunking (one
    # request per prompt).
    ai_chunk_chars: int = 60000
    ai_chunk_overlap_chars: int = 800
    ai_max_chunks_per_analysis: int = 20

    # Distributed state (spec 1.14.21-22). When set, DLP sliding windows,
    # escalation throttles/incidents and WebSocket fanout live in Redis so
    # they survive multiple API replicas + the Celery worker; unset falls
    # back to in-process state (single-instance deployments / tests).
    redis_url: str | None = None

    # External timestamp authority (spec 1.20.16). When set, audit batches
    # are anchored with an RFC 3161 token from this TSA in addition to the
    # Merkle root; unset keeps batches honest but internal_only.
    tsa_url: str | None = None
    tsa_username: str | None = None
    tsa_password: SecretStr | None = None
    tsa_policy_oid: str | None = None
    tsa_request_certificate: bool = False
    tsa_timeout_seconds: float = 10.0

    # Embeddings for contract-intelligence retrieval (spec 2.10.3-2.10.6).
    # 'openai' requires OPENAI_API_KEY and fails closed when unavailable;
    # 'hash' is the deterministic local fallback (dev/test, SQLite suite).
    # embedding_dim must match the pgvector column / HNSW index.
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # Incoming billing webhook (spec 22 "Incoming webhook security").
    # HMAC-SHA256 secret shared with the provider; when unset the webhook
    # endpoint refuses to process events.
    billing_webhook_secret: SecretStr | None = None
    billing_webhook_timestamp_skew_seconds: int = 300

    # Currency (spec §71 — no hardcoded business data). Base currency for
    # signing-authority / DOA / policy thresholds and the simplified FX table
    # (currency -> units of base currency per 1 unit). Feed FX_RATES_JSON from
    # a rates provider in production.
    default_currency: str = "LKR"
    fx_rates_json: str = '{"LKR": 1.0, "USD": 300.0, "EUR": 330.0, "GBP": 380.0, "SGD": 225.0, "INR": 3.6}'

    # Push notifications (spec 2.02 §24-26): Firebase service-account JSON
    # for the FCM admin SDK. When unset, push delivery degrades to a no-op
    # (WebSocket + email still deliver) — never an error path.
    fcm_credentials_json: SecretStr | None = None

    # SMS channel (spec §44: in-app, email, push, SMS, webhook). Generic
    # HTTP SMS gateway: POST {base_url} with bearer auth and
    # {"to", "from", "text"} — compatible with most provider bridges
    # (Twilio proxy, Africa's Talking, local SMS gateways). When the base
    # URL or key is unset the SMS channel degrades to a no-op, mirroring
    # the push channel: a missing credential must never break the outbox.
    sms_api_base_url: str | None = None
    sms_api_key: SecretStr | None = None
    sms_sender_id: str = "ContractOS"
    sms_timeout_seconds: float = 10.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_settings_uncached() -> Settings:
    return Settings()


# Lazy initialization - only load settings when first accessed
_settings: Settings | None = None


def get_settings_lazy() -> Settings:
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings
