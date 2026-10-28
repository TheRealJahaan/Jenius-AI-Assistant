"""Every setting Jenius needs, read once from the environment (or .env) and checked up front.

A missing or wrong value fails at startup with a clear message instead of halfway through
someone's sign-up. Production gets stricter checks than a laptop.
"""
import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# USD per million tokens: (input, output, cache read). Cache writes bill at 1.25x input.
# Source: Anthropic pricing, Oct 2026.
PRICES = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    jenius_env: Literal["development", "production", "test"] = "development"
    database_url: str = "sqlite:///./data/jenius.db"
    public_url: str = "http://localhost:8000"
    allowed_origins: str = ""
    cookie_secure: bool | None = None  # None = on in production, off on http://localhost

    llm_provider: Literal["anthropic", "offline"] = "offline"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_max_steps: int = Field(8, ge=1, le=20)

    free_daily_messages: int = Field(25, ge=0)
    pro_daily_messages: int = Field(300, ge=0)

    max_upload_mb: int = Field(5, ge=1, le=100)
    free_max_rows: int = Field(5000, ge=100)
    pro_max_rows: int = Field(200_000, ge=100)
    free_max_datasets: int = Field(3, ge=1)
    pro_max_datasets: int = Field(50, ge=1)

    upi_id: str = ""
    support_email: str = ""
    pro_monthly_inr: int = Field(799, ge=1)
    pro_annual_inr: int = Field(6708, ge=1)

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Jenius <no-reply@example.com>"
    smtp_starttls: bool = True

    session_days: int = Field(30, ge=1, le=365)

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, value: str) -> str:
        # Hosting providers hand out postgres:// URLs; SQLAlchemy needs the psycopg 3 driver named.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("must be a full address such as https://jenius.example.com")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_combinations(self) -> "Settings":
        problems = []
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.jenius_env == "production":
            if not self.database_url.startswith("postgresql"):
                problems.append("DATABASE_URL must be PostgreSQL in production (SQLite is for your laptop)")
            if not self.public_url.startswith("https://"):
                problems.append("PUBLIC_URL must start with https:// in production")
            if self.cookie_secure is False:
                problems.append("COOKIE_SECURE cannot be false in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_url.startswith("https://")

    @property
    def origins(self) -> set[str]:
        """Browser origins allowed to send state-changing requests (the CSRF check)."""
        parsed = urlparse(self.public_url)
        allowed = {f"{parsed.scheme}://{parsed.netloc}"}
        allowed.update(o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip())
        return allowed

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider == "anthropic"


def load_settings(**overrides) -> Settings:
    """Read and check the settings, turning pydantic's errors into one readable message."""
    # On Render the public address is known without configuring it.
    if "public_url" not in overrides and not os.environ.get("PUBLIC_URL") and os.environ.get("RENDER_EXTERNAL_URL"):
        overrides["public_url"] = os.environ["RENDER_EXTERNAL_URL"]
    try:
        return Settings(**overrides)
    except ValidationError as err:
        lines = []
        for issue in err.errors():
            where = ".".join(str(p) for p in issue["loc"]).upper() or "settings"
            lines.append(f"  - {where}: {issue['msg'].removeprefix('Value error, ')}")
        raise SystemExit(
            "Jenius cannot start. Fix these settings in .env or the environment:\n"
            + "\n".join(lines)
            + "\nSee .env.example for what each value means."
        ) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
