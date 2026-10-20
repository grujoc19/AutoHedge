"""Explicit settings with process-environment precedence and protected secrets."""

import os
from pathlib import Path
from typing import Literal, Mapping

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError
from pydantic import field_validator

from autohedge.errors import ConfigurationError


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    exa_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    jupiter_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    massive_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    solana_private_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    model: str = Field(default="gpt-4.1", min_length=1, max_length=100)
    sentiment_model: str = Field(default="gpt-4o-mini", min_length=1, max_length=100)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path | None = None
    request_timeout: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    cycle_timeout: float = Field(default=180, gt=0, le=600, allow_inf_nan=False)
    search_enabled: bool = True
    exa_base_url: HttpUrl = HttpUrl("https://api.exa.ai")
    jupiter_base_url: HttpUrl = HttpUrl("https://api.jup.ag")
    massive_base_url: HttpUrl = HttpUrl("https://api.massive.com")

    @field_validator(
        "openai_api_key",
        "exa_api_key",
        "jupiter_api_key",
        "massive_api_key",
        "solana_private_key",
        mode="before",
    )
    @classmethod
    def blank_secrets(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("exa_base_url", "jupiter_base_url", "massive_base_url")
    @classmethod
    def https_without_credentials(cls, value):
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError(
                "API base URLs must use HTTPS without credentials or query"
            )
        return value

    @field_validator("model", "sentiment_model")
    @classmethod
    def nonblank_model(cls, value):
        if not value.strip():
            raise ValueError("Model name must not be blank")
        return value.strip()

    def secret_values(self) -> tuple[str, ...]:
        return tuple(
            secret.get_secret_value()
            for name in (
                "openai_api_key",
                "exa_api_key",
                "jupiter_api_key",
                "massive_api_key",
                "solana_private_key",
            )
            if (secret := getattr(self, name)) is not None
        )

    def require_research_credentials(self):
        if not self.openai_api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is required for model-backed research"
            )
        if self.search_enabled and not self.exa_api_key:
            raise ConfigurationError("EXA_API_KEY is required when search is enabled")

    def headers(self, provider: str) -> dict[str, str]:
        secret = getattr(self, f"{provider}_api_key", None)
        if secret is None:
            raise ConfigurationError(f"Missing API key for {provider}")
        key = secret.get_secret_value()
        return (
            {"Authorization": f"Bearer {key}"}
            if provider == "massive"
            else {"x-api-key": key}
        )


ENV_FIELDS = {
    "OPENAI_API_KEY": "openai_api_key",
    "EXA_API_KEY": "exa_api_key",
    "JUPITER_API_KEY": "jupiter_api_key",
    "MASSIVE_API_KEY": "massive_api_key",
    "SOLANA_PRIVATE_KEY": "solana_private_key",
    "AUTOHEDGE_MODEL": "model",
    "AUTOHEDGE_SENTIMENT_MODEL": "sentiment_model",
    "AUTOHEDGE_LOG_LEVEL": "log_level",
    "AUTOHEDGE_LOG_FILE": "log_file",
    "AUTOHEDGE_REQUEST_TIMEOUT": "request_timeout",
    "AUTOHEDGE_CYCLE_TIMEOUT": "cycle_timeout",
    "AUTOHEDGE_SEARCH_ENABLED": "search_enabled",
    "AUTOHEDGE_EXA_BASE_URL": "exa_base_url",
    "AUTOHEDGE_JUPITER_BASE_URL": "jupiter_base_url",
    "MASSIVE_BASE_URL": "massive_base_url",
}


def load_settings(
    *, environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> Settings:
    """Read dotenv without modifying os.environ; explicit environment wins, even empty."""
    if env_file is None:
        from autohedge.env_loader import find_project_env

        env_file = find_project_env()
    file_values = dotenv_values(env_file, interpolate=False) if env_file else {}
    process = dict(os.environ if environ is None else environ)
    values = {**file_values, **process}
    # Apply aliases within each source so process bindings also beat file aliases.
    aliases = {
        "AUTOHEDGE_OPENAI_API_KEY": "OPENAI_API_KEY",
        "POLYGON_BASE_URL": "MASSIVE_BASE_URL",
    }
    for alias, target in aliases.items():
        if target not in process and alias in process:
            values[target] = process[alias]
        elif target not in values and alias in file_values:
            values[target] = file_values[alias]
    inputs = {
        field: values[name] for name, field in ENV_FIELDS.items() if name in values
    }
    for name in (
        "openai_api_key",
        "exa_api_key",
        "jupiter_api_key",
        "massive_api_key",
        "solana_private_key",
        "log_file",
    ):
        if name in inputs and not inputs[name]:
            inputs[name] = None
    try:
        return Settings(**inputs)
    except ValidationError:
        raise ConfigurationError(
            "Invalid AutoHedge configuration; check environment settings"
        ) from None
