"""Provider-neutral model contracts; importing this package initializes no SDK."""

from autohedge.ai.client import ModelProvider, complete_model
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
    "ModelCallError",
    "ModelErrorCode",
    "ModelMessage",
    "ModelProvider",
    "ModelReply",
    "ModelRequest",
    "ModelResult",
    "ModelTarget",
    "TokenUsage",
    "complete_model",
]
