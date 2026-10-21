"""Immutable role profiles; configuration does not activate a model provider."""

from enum import Enum
import re

from pydantic import model_validator

from autohedge.ai.contracts import ModelTarget, OutputTokenLimit, Seconds
from autohedge.contracts import Contract


def native_gemini_model(model: str) -> str:
    native = model.removeprefix("gemini/")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", native) is None:
        raise ValueError("Invalid native Gemini model identifier")
    return native


class AgentRole(str, Enum):
    NEWS_INTELLIGENCE = "news_intelligence"
    MARKET_ANOMALY = "market_anomaly"
    ON_CHAIN_INTELLIGENCE = "on_chain_intelligence"
    QUANT = "quant"
    TRADING_DIRECTOR = "trading_director"
    RISK_MANAGER = "risk_manager"


class ModelBackend(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"


class RoleModelOverride(Contract):
    target: ModelTarget
    max_output_tokens: OutputTokenLimit = 1500
    timeout_seconds: Seconds | None = None

    @model_validator(mode="after")
    def supported_backend(self):
        ModelBackend(self.target.provider)
        return self


class RoleModelConfig(RoleModelOverride):
    """Resolved profile with no implicit timeout left for a caller to guess."""

    timeout_seconds: Seconds


class RoleModels(Contract):
    news_intelligence: RoleModelOverride | None = None
    market_anomaly: RoleModelOverride | None = None
    on_chain_intelligence: RoleModelOverride | None = None
    quant: RoleModelOverride | None = None
    trading_director: RoleModelOverride | None = None
    risk_manager: RoleModelOverride | None = None


LEGACY_RESEARCH_ROLES = (
    AgentRole.TRADING_DIRECTOR,
    AgentRole.QUANT,
    AgentRole.NEWS_INTELLIGENCE,
    AgentRole.RISK_MANAGER,
)
