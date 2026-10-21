"""Safe, enumerated model failures with explicit usage uncertainty."""

from enum import Enum
from uuid import UUID

from autohedge.ai.contracts import TokenUsage
from autohedge.errors import AutoHedgeError


class ModelErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    RESPONSE_MISMATCH = "response_mismatch"
    OUTPUT_LIMIT = "output_limit"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    PROVIDER_FAILURE = "provider_failure"
    TRUNCATED = "truncated"
    REFUSED = "refused"
    INVALID_CLOCK = "invalid_clock"


class ModelCallError(AutoHedgeError):
    """No raw exception, provider payload, URL, prompt or credential in the message."""

    def __init__(
        self,
        code: ModelErrorCode,
        *,
        request_id: UUID | None = None,
        usage: TokenUsage | None = None,
    ):
        self.code = ModelErrorCode(code)
        self.request_id = request_id
        self.usage = (
            TokenUsage.model_validate(usage)
            if usage is not None
            else TokenUsage(status="unknown")
        )
        super().__init__(f"Model call failed: {self.code.value}")
