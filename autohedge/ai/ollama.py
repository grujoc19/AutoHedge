"""One native Ollama chat POST; no downloads, tool calls or cloud fallback."""

from typing import Literal

import httpx
from pydantic import BaseModel, StrictBool, ValidationError

from autohedge.ai.contracts import (
    ModelName,
    ModelReply,
    ModelRequest,
    TokenCount,
    TokenUsage,
)
from autohedge.ai.errors import ModelCallError, ModelErrorCode
from autohedge.config import Settings
from autohedge.errors import ProviderError
from autohedge.providers import request_json


class _Message(BaseModel):
    role: Literal["assistant"]
    content: str
    thinking: str | None = None
    tool_calls: list | None = None


class _Response(BaseModel):
    model: ModelName
    message: _Message
    done: StrictBool
    done_reason: Literal["stop", "length"]
    prompt_eval_count: TokenCount | None = None
    eval_count: TokenCount | None = None
    remote_model: str | None = None
    remote_host: str | None = None
    error: str | None = None


class OllamaProvider:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        request = ModelRequest.model_validate(request)
        if request.target.provider != "ollama":
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        try:
            payload = request_json(
                "ollama",
                "POST",
                str(self.settings.ollama_base_url).rstrip("/") + "/api/chat",
                settings=self.settings.model_copy(
                    update={"request_timeout": request.timeout_seconds}
                ),
                client=self.client,
                precise=True,
                headers={"X-Client-Request-Id": str(request.request_id)},
                json={
                    "model": request.target.model,
                    "messages": [message.model_dump() for message in request.messages],
                    "options": {"num_predict": request.max_output_tokens},
                    "stream": False,
                    "think": False,
                    "truncate": False,
                    "shift": False,
                },
            )
        except ProviderError as error:
            code = {
                401: ModelErrorCode.AUTHENTICATION,
                403: ModelErrorCode.AUTHENTICATION,
                429: ModelErrorCode.RATE_LIMITED,
            }.get(
                error.status_code,
                {
                    "request timed out": ModelErrorCode.TIMEOUT,
                    "connection failed": ModelErrorCode.UNAVAILABLE,
                    "invalid JSON response": ModelErrorCode.INVALID_RESPONSE,
                }.get(error.reason, ModelErrorCode.PROVIDER_FAILURE),
            )
            raise ModelCallError(code) from None
        try:
            response = _Response.model_validate(payload)
            inputs, outputs = response.prompt_eval_count, response.eval_count
            count = sum(value is not None for value in (inputs, outputs))
            usage = TokenUsage(
                status={0: "unknown", 1: "partial", 2: "known"}[count],
                input_tokens=inputs,
                output_tokens=outputs,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE) from None
        if (
            not response.done
            or response.message.tool_calls
            or response.message.thinking
            or response.remote_model
            or response.remote_host
            or response.error is not None
        ):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        reason = "complete" if response.done_reason == "stop" else "length"
        try:
            return ModelReply(
                request_id=request.request_id,
                target=request.target,
                resolved_model=response.model,
                finish_reason=reason,
                content=response.message.content if reason == "complete" else None,
                usage=usage,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage) from None
