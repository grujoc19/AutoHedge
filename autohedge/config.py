"""Explicit settings with process-environment precedence and protected secrets."""

import os
from pathlib import Path
from typing import Literal, Mapping

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError
from pydantic import field_validator

from autohedge.ai.config import (
    AgentRole,
    LEGACY_RESEARCH_ROLES,
    ModelBackend,
    RoleModelConfig,
    RoleModels,
    native_gemini_model,
)
from autohedge.ai.contracts import ModelTarget, Seconds
from autohedge.errors import ConfigurationError
from autohedge.data.contracts import QualityPolicy


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    anthropic_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    gemini_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    deepseek_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    exa_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    jupiter_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    massive_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    coingecko_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    solana_private_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    model: str = Field(default="gpt-4.1", min_length=1, max_length=100)
    sentiment_model: str = Field(default="gpt-4o-mini", min_length=1, max_length=100)
    role_models: RoleModels = Field(default_factory=RoleModels)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path | None = None
    request_timeout: Seconds = 30
    cycle_timeout: float = Field(default=180, gt=0, le=600, allow_inf_nan=False)
    search_enabled: bool = True
    openai_base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    anthropic_base_url: HttpUrl = HttpUrl("https://api.anthropic.com/v1")
    gemini_base_url: HttpUrl = HttpUrl(
        "https://generativelanguage.googleapis.com/v1beta"
    )
    ollama_base_url: HttpUrl = HttpUrl("http://127.0.0.1:11434")
    exa_base_url: HttpUrl = HttpUrl("https://api.exa.ai")
    jupiter_base_url: HttpUrl = HttpUrl("https://api.jup.ag")
    massive_base_url: HttpUrl = HttpUrl("https://api.massive.com")
    coingecko_base_url: HttpUrl = HttpUrl("https://pro-api.coingecko.com/api/v3")
    market_quality: QualityPolicy = Field(default_factory=QualityPolicy)

    @field_validator(
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "deepseek_api_key",
        "exa_api_key",
        "jupiter_api_key",
        "massive_api_key",
        "coingecko_api_key",
        "solana_private_key",
        mode="before",
    )
    @classmethod
    def blank_secrets(cls, value):
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "openai_base_url",
        "anthropic_base_url",
        "gemini_base_url",
        "exa_base_url",
        "jupiter_base_url",
        "massive_base_url",
        "coingecko_base_url",
    )
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

    @field_validator("ollama_base_url")
    @classmethod
    def ollama_without_credentials(cls, value):
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Ollama base URL must not contain credentials or query")
        return value

    def secret_values(self) -> tuple[str, ...]:
        return tuple(
            secret.get_secret_value()
            for name in (
                "openai_api_key",
                "anthropic_api_key",
                "gemini_api_key",
                "deepseek_api_key",
                "exa_api_key",
                "jupiter_api_key",
                "massive_api_key",
                "coingecko_api_key",
                "solana_private_key",
            )
            if (secret := getattr(self, name)) is not None
        )

    def model_for(self, role: AgentRole | str) -> RoleModelConfig:
        """Resolve a role without keys, SDK imports, environment changes or calls."""
        try:
            role = AgentRole(role)
            profiles = RoleModels.model_validate(self.role_models)
            override = getattr(profiles, role.value)
            if override is not None:
                values = override.model_dump()
                if override.timeout_seconds is None:
                    values["timeout_seconds"] = self.request_timeout
                return RoleModelConfig.model_validate(values)
            return RoleModelConfig(
                target=ModelTarget(
                    provider="openai",
                    model=self.sentiment_model
                    if role == AgentRole.NEWS_INTELLIGENCE
                    else self.model,
                ),
                timeout_seconds=self.request_timeout,
            )
        except (ValueError, TypeError):
            raise ConfigurationError("Invalid role model configuration") from None

    def model_api_key(self, role: AgentRole | str) -> SecretStr | None:
        backend = ModelBackend(self.model_for(role).target.provider)
        return (
            None
            if backend == ModelBackend.OLLAMA
            else getattr(self, f"{backend.value}_api_key")
        )

    def require_model_credentials(self, role: AgentRole | str):
        backend = ModelBackend(self.model_for(role).target.provider)
        if backend != ModelBackend.OLLAMA and not self.model_api_key(role):
            raise ConfigurationError(
                f"{backend.value.upper()}_API_KEY is required for the selected role"
            )

    def require_research_model_configuration(self):
        for role in LEGACY_RESEARCH_ROLES:
            config = self.model_for(role)
            if config.target.provider not in {
                "openai",
                "ollama",
                "anthropic",
                "gemini",
            }:
                raise ConfigurationError(
                    "Selected research provider is not implemented yet"
                )
            if config.target.provider in {"openai", "anthropic"}:
                native_model = config.target.model.removeprefix(
                    config.target.provider + "/"
                )
                if not native_model or "/" in native_model:
                    raise ConfigurationError("Invalid native cloud model identifier")
            if config.target.provider == "gemini":
                try:
                    native_gemini_model(config.target.model)
                except ValueError:
                    raise ConfigurationError(
                        "Invalid native Gemini model identifier"
                    ) from None

    def require_research_credentials(self):
        self.require_research_model_configuration()
        for role in LEGACY_RESEARCH_ROLES:
            self.require_model_credentials(role)
        if self.search_enabled and not self.exa_api_key:
            raise ConfigurationError("EXA_API_KEY is required when search is enabled")

    def headers(self, provider: str) -> dict[str, str]:
        secret = getattr(self, f"{provider}_api_key", None)
        if secret is None:
            raise ConfigurationError(f"Missing API key for {provider}")
        key = secret.get_secret_value()
        if provider == "massive":
            return {"Authorization": f"Bearer {key}"}
        if provider == "coingecko":
            return {"x-cg-pro-api-key": key}
        return {"x-api-key": key}


ENV_FIELDS = {
    "OPENAI_API_KEY": "openai_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "GEMINI_API_KEY": "gemini_api_key",
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "EXA_API_KEY": "exa_api_key",
    "JUPITER_API_KEY": "jupiter_api_key",
    "MASSIVE_API_KEY": "massive_api_key",
    "COINGECKO_PRO_API_KEY": "coingecko_api_key",
    "SOLANA_PRIVATE_KEY": "solana_private_key",
    "AUTOHEDGE_MODEL": "model",
    "AUTOHEDGE_SENTIMENT_MODEL": "sentiment_model",
    "AUTOHEDGE_LOG_LEVEL": "log_level",
    "AUTOHEDGE_LOG_FILE": "log_file",
    "AUTOHEDGE_REQUEST_TIMEOUT": "request_timeout",
    "AUTOHEDGE_CYCLE_TIMEOUT": "cycle_timeout",
    "AUTOHEDGE_SEARCH_ENABLED": "search_enabled",
    "AUTOHEDGE_OPENAI_BASE_URL": "openai_base_url",
    "AUTOHEDGE_ANTHROPIC_BASE_URL": "anthropic_base_url",
    "AUTOHEDGE_GEMINI_BASE_URL": "gemini_base_url",
    "AUTOHEDGE_OLLAMA_BASE_URL": "ollama_base_url",
    "AUTOHEDGE_EXA_BASE_URL": "exa_base_url",
    "AUTOHEDGE_JUPITER_BASE_URL": "jupiter_base_url",
    "MASSIVE_BASE_URL": "massive_base_url",
    "AUTOHEDGE_COINGECKO_BASE_URL": "coingecko_base_url",
}

ROLE_ENV_SUFFIXES = {
    "PROVIDER": "provider",
    "MODEL": "model",
    "MAX_OUTPUT_TOKENS": "max_output_tokens",
    "TIMEOUT": "timeout_seconds",
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
        "GOOGLE_API_KEY": "GEMINI_API_KEY",
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
        "anthropic_api_key",
        "gemini_api_key",
        "deepseek_api_key",
        "exa_api_key",
        "jupiter_api_key",
        "massive_api_key",
        "coingecko_api_key",
        "solana_private_key",
        "log_file",
    ):
        if name in inputs and not inputs[name]:
            inputs[name] = None
    try:
        role_env_names = {
            f"AUTOHEDGE_{role.value.upper()}_{suffix}"
            for role in AgentRole
            for suffix in ROLE_ENV_SUFFIXES
        }
        role_prefixes = tuple(f"AUTOHEDGE_{role.value.upper()}_" for role in AgentRole)
        for name in values:
            if name.startswith("AUTOHEDGE_") and name not in ENV_FIELDS:
                if (
                    name.startswith(role_prefixes)
                    or name.endswith(
                        tuple("_" + suffix for suffix in ROLE_ENV_SUFFIXES)
                    )
                ) and name not in role_env_names:
                    raise ConfigurationError("Unknown role model environment setting")
        profiles = {}
        for role in AgentRole:
            prefix = f"AUTOHEDGE_{role.value.upper()}_"
            fields = {
                field: values[prefix + suffix]
                for suffix, field in ROLE_ENV_SUFFIXES.items()
                if prefix + suffix in values
            }
            if fields:
                if any(value is None for value in fields.values()):
                    raise ValueError("Role environment settings require values")
                target = {
                    "provider": fields.pop("provider", "openai"),
                    "model": fields.pop("model", None),
                }
                tokens = fields.get("max_output_tokens")
                if (
                    isinstance(tokens, str)
                    and tokens.strip().isascii()
                    and tokens.strip().isdecimal()
                ):
                    fields["max_output_tokens"] = int(tokens)
                profiles[role.value] = {"target": target, **fields}
        if profiles:
            inputs["role_models"] = profiles
        return Settings(**inputs)
    except (ValidationError, ValueError, TypeError):
        raise ConfigurationError(
            "Invalid AutoHedge configuration; check environment settings"
        ) from None
