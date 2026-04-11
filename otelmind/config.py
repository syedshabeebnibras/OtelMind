"""Application configuration via environment variables using dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with a fallback default."""
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    """Read an environment variable as int."""
    val = os.getenv(key)
    if val is None:
        return default
    return int(val)


def _env_bool(key: str, default: bool = False) -> bool:
    """Read an environment variable as bool."""
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass
class DatabaseConfig:
    """Database connection settings."""

    host: str = field(default_factory=lambda: _env("POSTGRES_HOST", "localhost"))
    port: int = field(default_factory=lambda: _env_int("POSTGRES_PORT", 5432))
    database: str = field(default_factory=lambda: _env("POSTGRES_DB", "otelmind"))
    user: str = field(default_factory=lambda: _env("POSTGRES_USER", "otelmind"))
    password: str = field(default_factory=lambda: _env("POSTGRES_PASSWORD", "otelmind"))


@dataclass
class LLMConfig:
    """LLM provider settings."""

    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai"))
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4"))
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    api_base: str = field(default_factory=lambda: _env("LLM_API_BASE"))
    api_version: str = field(default_factory=lambda: _env("LLM_API_VERSION"))


@dataclass
class OtelConfig:
    """OpenTelemetry settings."""

    endpoint: str = field(
        default_factory=lambda: _env("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    )
    service_name: str = field(default_factory=lambda: _env("OTEL_SERVICE_NAME", "otelmind"))


@dataclass
class RemediationConfig:
    """Remediation / retry settings."""

    retry_max_attempts: int = field(default_factory=lambda: _env_int("RETRY_MAX_ATTEMPTS", 3))
    retry_backoff_base: float = field(
        default_factory=lambda: float(_env("RETRY_BACKOFF_BASE", "2.0")),
    )
    escalation_webhook_url: str = field(
        default_factory=lambda: _env("ESCALATION_WEBHOOK_URL"),
    )
    fallback_tool_registry: str = field(
        default_factory=lambda: _env("FALLBACK_TOOL_REGISTRY"),
    )


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """Top-level application configuration that aggregates all sub-configs."""

    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    otel: OtelConfig = field(default_factory=OtelConfig)
    remediation: RemediationConfig = field(default_factory=RemediationConfig)

    # API settings
    api_host: str = field(default_factory=lambda: _env("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: _env_int("API_PORT", 8000))

    # Watchdog settings
    watchdog_interval_seconds: int = field(
        default_factory=lambda: _env_int("WATCHDOG_INTERVAL_SECONDS", 30),
    )
    watchdog_llm_judge_enabled: bool = field(
        default_factory=lambda: _env_bool("WATCHDOG_LLM_JUDGE_ENABLED", False),
    )

    # API reload flag
    api_reload: bool = field(default_factory=lambda: _env_bool("API_RELOAD", False))

    # DB pool tuning
    db_pool_size: int = field(default_factory=lambda: _env_int("DB_POOL_SIZE", 20))
    db_max_overflow: int = field(default_factory=lambda: _env_int("DB_MAX_OVERFLOW", 10))

    # Redis
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379"))

    # Auth / API keys
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "change-me-in-production"))
    api_key_prefix: str = field(default_factory=lambda: _env("API_KEY_PREFIX", "om_"))

    # Rate limits (requests per minute per tenant)
    rate_limit_ingest: int = field(default_factory=lambda: _env_int("RATE_LIMIT_INGEST", 10_000))
    rate_limit_read: int = field(default_factory=lambda: _env_int("RATE_LIMIT_READ", 500))

    # ─ Eval / continuous quality ─────────────────────────────────────
    # How often (seconds) the background worker polls for pending
    # EvalRun rows and ships them through run_regression.
    eval_worker_interval_seconds: int = field(
        default_factory=lambda: _env_int("EVAL_WORKER_INTERVAL_SECONDS", 15),
    )
    # How often the auto-scoring loop samples new traces.
    eval_autoscorer_interval_seconds: int = field(
        default_factory=lambda: _env_int("EVAL_AUTOSCORER_INTERVAL_SECONDS", 60),
    )
    # Fraction of new traces to score (0.0 = off, 1.0 = every trace).
    eval_autoscorer_sample_rate: float = field(
        default_factory=lambda: float(_env("EVAL_AUTOSCORER_SAMPLE_RATE", "0.1")),
    )
    # How many traces at most to score per tick, even at 100% sampling —
    # caps the LLM spend on high-traffic tenants.
    eval_autoscorer_batch_size: int = field(
        default_factory=lambda: _env_int("EVAL_AUTOSCORER_BATCH_SIZE", 5),
    )
    # Daily golden-dataset regression cron. Dataset path is YAML/JSON,
    # format documented in config/eval_datasets/README.md.
    eval_golden_dataset_path: str = field(
        default_factory=lambda: _env(
            "EVAL_GOLDEN_DATASET_PATH", "config/eval_datasets/golden.yaml"
        ),
    )
    # Fail the regression (and alert) if any dimension drops by more
    # than this fraction vs the previous day's run.
    eval_regression_threshold: float = field(
        default_factory=lambda: float(_env("EVAL_REGRESSION_THRESHOLD", "0.05")),
    )
    # Run the daily golden regression at this UTC hour (0-23).
    eval_daily_run_utc_hour: int = field(
        default_factory=lambda: _env_int("EVAL_DAILY_RUN_UTC_HOUR", 2),
    )

    # Alerting
    slack_default_webhook: str = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL", ""))
    pagerduty_routing_key: str = field(default_factory=lambda: _env("PAGERDUTY_ROUTING_KEY", ""))
    alert_email_from: str = field(default_factory=lambda: _env("ALERT_EMAIL_FROM", ""))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD", ""))

    # ------------------------------------------------------------------
    # Backward-compatible computed properties
    # ------------------------------------------------------------------

    @property
    def database_url(self) -> str:
        """Async database URL built from DatabaseConfig fields."""
        return (
            f"postgresql+asyncpg://{self.db.user}:{self.db.password}"
            f"@{self.db.host}:{self.db.port}/{self.db.database}"
        )

    @property
    def database_url_sync(self) -> str:
        """Synchronous database URL built from DatabaseConfig fields."""
        return (
            f"postgresql://{self.db.user}:{self.db.password}"
            f"@{self.db.host}:{self.db.port}/{self.db.database}"
        )

    @property
    def remediation_webhook_url(self) -> str:
        """Alias for remediation.escalation_webhook_url."""
        return self.remediation.escalation_webhook_url

    @property
    def remediation_max_retries(self) -> int:
        """Alias for remediation.retry_max_attempts."""
        return self.remediation.retry_max_attempts

    @property
    def otel_service_name(self) -> str:
        """Alias for otel.service_name."""
        return self.otel.service_name


# ---------------------------------------------------------------------------
# Module-level singleton for backward compatibility
# ---------------------------------------------------------------------------

settings = AppConfig()
