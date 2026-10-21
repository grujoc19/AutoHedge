"""One OpenAI Chat Completions POST through the existing safe HTTP transport."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from autohedge.ai.contracts import (
    ModelName,
    ModelReply,
    ModelRequest,
    TokenCount,
    TokenUsage,
)
from autohedge.ai.errors import ModelCallError, ModelErrorCode
from autohedge.config import Settings
from autohedge.errors import ConfigurationError, ProviderError
from autohedge.providers import request_json


class _Usage(BaseModel):
    prompt_tokens: TokenCount | None = None
    completion_tokens: TokenCount | None = None


class _Message(BaseModel):
    role: Literal["assistant"]
    content: str | None = None
    refusal: str | None = None
    tool_calls: list | None = None
    function_call: dict | None = None


class _Choice(BaseModel):
    index: StrictInt
    finish_reason: Literal[
        "stop", "length", "content_filter", "tool_calls", "function_call"
    ]
    message: _Message


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: ModelName
    choices: list[_Choice] = Field(min_length=1, max_length=1)
    usage: _Usage | None = None


class OpenAIProvider:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        request = ModelRequest.model_validate(request)
        if request.target.provider != "openai":
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        model = request.target.model.removeprefix("openai/")
        if not model or "/" in model:
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        if self.settings.openai_api_key is None:
            raise ConfigurationError(
                "OPENAI_API_KEY is required for model-backed research"
            )
        try:
            payload = request_json(
                "openai",
                "POST",
                str(self.settings.openai_base_url).rstrip("/") + "/chat/completions",
                settings=self.settings.model_copy(
                    update={"request_timeout": request.timeout_seconds}
                ),
                client=self.client,
                precise=True,
                headers={
                    "Authorization": "Bearer "
                    + self.settings.openai_api_key.get_secret_value(),
                    "X-Client-Request-Id": str(request.request_id),
                },
                json={
                    "model": model,
                    "messages": [message.model_dump() for message in request.messages],
                    "max_completion_tokens": request.max_output_tokens,
                    "stream": False,
                    "n": 1,
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
            inputs = response.usage.prompt_tokens if response.usage else None
            outputs = response.usage.completion_tokens if response.usage else None
            count = sum(value is not None for value in (inputs, outputs))
            usage = TokenUsage(
                status={0: "unknown", 1: "partial", 2: "known"}[count],
                input_tokens=inputs,
                output_tokens=outputs,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE) from None
        choice = response.choices[0]
        if (
            choice.index != 0
            or choice.message.tool_calls
            or choice.message.function_call
            or choice.finish_reason in {"tool_calls", "function_call"}
        ):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        reason = (
            "refusal"
            if choice.message.refusal is not None
            or choice.finish_reason == "content_filter"
            else "length"
            if choice.finish_reason == "length"
            else "complete"
        )
        try:
            return ModelReply(
                request_id=request.request_id,
                target=request.target,
                resolved_model=response.model,
                finish_reason=reason,
                content=choice.message.content if reason == "complete" else None,
                usage=usage,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage) from None
