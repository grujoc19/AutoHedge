"""Provider-neutral model contracts; importing this package initializes no SDK."""

from autohedge.ai.client import ModelProvider, complete_model
from autohedge.ai.config import (
    AgentRole,
    ModelBackend,
    RoleModelConfig,
    RoleModelOverride,
    RoleModels,
)
from autohedge.ai.contracts import (
    ModelMessage,
    ModelReply,
    ModelRequest,
    ModelResult,
    ModelTarget,
    TokenUsage,
)
from autohedge.ai.errors import ModelCallError, ModelErrorCode

__all__ = [
    "AgentRole",
    "ModelBackend",
    "ModelCallError",
    "ModelErrorCode",
    "ModelMessage",
    "ModelProvider",
    "ModelReply",
    "ModelRequest",
    "ModelResult",
    "ModelTarget",
    "RoleModelConfig",
    "RoleModelOverride",
    "RoleModels",
    "TokenUsage",
    "complete_model",
]
