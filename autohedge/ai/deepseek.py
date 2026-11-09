"""Non-thinking DeepSeek with LiteLLM formatting and shared Chat validation."""

from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from autohedge.ai.chat_completions import (
    ChatChoice,
    ChatMessage,
    ChatResponse,
    ChatUsage,
    reply_from_response,
    token_usage,
)
from autohedge.ai.config import native_deepseek_model
from autohedge.ai.contracts import (
    ModelMessage,
    ModelReply,
    ModelRequest,
    TokenCount,
    TokenUsage,
)
from autohedge.ai.errors import ModelCallError, ModelErrorCode
from autohedge.config import Settings
from autohedge.errors import ConfigurationError, ProviderError
from autohedge.providers import request_json


class _Details(BaseModel):
    reasoning_tokens: TokenCount | None = None


class _Usage(ChatUsage):
    total_tokens: TokenCount | None = None
    prompt_cache_hit_tokens: TokenCount | None = None
    prompt_cache_miss_tokens: TokenCount | None = None
    completion_tokens_details: _Details | None = None


class _Message(ChatMessage):
    reasoning_content: str | None = None


class _Choice(ChatChoice):
    finish_reason: Literal[
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "function_call",
        "insufficient_system_resource",
    ]
    message: _Message


class _Response(ChatResponse):
    choices: list[_Choice] = Field(min_length=1, max_length=1)
    usage: _Usage | None = None
    error: dict | None = None


def _usage(raw: _Usage | None) -> TokenUsage:
    if raw is None:
        return token_usage(None, None)
    inputs, outputs = raw.prompt_tokens, raw.completion_tokens
    hits, misses = raw.prompt_cache_hit_tokens, raw.prompt_cache_miss_tokens
    if hits is not None and misses is not None:
        if inputs is not None and inputs != hits + misses:
            raise ValueError("Inconsistent DeepSeek cache usage")
        inputs = hits + misses
    if raw.total_tokens is not None:
        if (
            inputs is not None
            and outputs is not None
            and inputs + outputs != raw.total_tokens
        ):
            raise ValueError("Inconsistent DeepSeek total usage")
        if inputs is None and outputs is not None:
            inputs = raw.total_tokens - outputs
        if outputs is None and inputs is not None:
            outputs = raw.total_tokens - inputs
    if inputs is not None and any(n is not None and n > inputs for n in (hits, misses)):
        raise ValueError("Inconsistent DeepSeek cache usage")
    reasoning = (
        raw.completion_tokens_details.reasoning_tokens
        if raw.completion_tokens_details
        else None
    )
    if raw.total_tokens is not None:
        minimum_input = (
            inputs
            if inputs is not None
            else sum(n for n in (hits, misses) if n is not None)
        )
        minimum_output = outputs if outputs is not None else reasoning
        if minimum_input + (minimum_output or 0) > raw.total_tokens:
            raise ValueError("Inconsistent DeepSeek total usage")
    if reasoning is not None and outputs is not None and reasoning > outputs:
        raise ValueError("Inconsistent DeepSeek reasoning usage")
    return token_usage(inputs, outputs)


def _messages(request: ModelRequest) -> list[dict]:
    original = request.messages
    non_system = [m for m in original if m.role != "system"]
    if non_system[0].role != "user" or non_system[-1].role != "user":
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
    try:
        from litellm.litellm_core_utils.prompt_templates.common_utils import (
            handle_messages_with_content_list_to_str_conversion,
        )

        formatted = handle_messages_with_content_list_to_str_conversion(
            messages=[m.model_dump() for m in original]
        )
        validated = tuple(ModelMessage.model_validate(m) for m in formatted)
        if validated != original:
            raise ValueError("Message formatter changed the transcript")
        return [m.model_dump() for m in validated]
    except Exception:
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST) from None


class DeepSeekProvider:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        request = ModelRequest.model_validate(request)
        if request.target.provider != "deepseek":
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        try:
            model = native_deepseek_model(request.target.model)
        except ValueError:
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST) from None
        if self.settings.deepseek_api_key is None:
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is required for model-backed research"
            )
        messages = _messages(request)
        try:
            payload = request_json(
                "deepseek",
                "POST",
                str(self.settings.deepseek_base_url).rstrip("/") + "/chat/completions",
                settings=self.settings.model_copy(
                    update={"request_timeout": request.timeout_seconds}
                ),
                client=self.client,
                precise=True,
                headers={
                    "Authorization": "Bearer "
                    + self.settings.deepseek_api_key.get_secret_value(),
                    "X-Client-Request-Id": str(request.request_id),
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": request.max_output_tokens,
                    "stream": False,
                    "thinking": {"type": "disabled"},
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
            usage = _usage(response.usage)
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE) from None
        choice = response.choices[0]
        if response.error is not None:
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        if choice.finish_reason == "insufficient_system_resource":
            raise ModelCallError(ModelErrorCode.UNAVAILABLE, usage=usage)
        if choice.message.reasoning_content or (
            response.usage
            and response.usage.completion_tokens_details
            and response.usage.completion_tokens_details.reasoning_tokens
        ):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        return reply_from_response(request, response, usage)
