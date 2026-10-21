"""LiteLLM text formatting with one application-owned Anthropic Messages POST."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class _WireText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str


class _WireMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: list[_WireText] = Field(min_length=1, max_length=64)


def _messages(request: ModelRequest, model: str) -> dict:
    """Use only LiteLLM's formatter; verify it preserves every role/text block."""
    messages = [m.model_dump() for m in request.messages if m.role != "system"]
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
    try:
        from litellm.litellm_core_utils.prompt_templates.factory import (
            anthropic_messages_pt,
        )

        converted = anthropic_messages_pt(
            messages=messages, model=model, llm_provider="anthropic"
        )
        wire = [_WireMessage.model_validate(message) for message in converted]
        if [(m.role, block.text) for m in wire for block in m.content] != [
            (m.role, m.content) for m in request.messages if m.role != "system"
        ]:
            raise ValueError("Message formatter changed the transcript")
    except Exception:
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST) from None
    payload = {"messages": [m.model_dump() for m in wire]}
    if request.messages[0].role == "system":
        payload["system"] = request.messages[0].content
    return payload


class _Usage(BaseModel):
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    cache_creation_input_tokens: TokenCount | None = None
    cache_read_input_tokens: TokenCount | None = None
    server_tool_use: dict[str, TokenCount] | None = None


class _Block(BaseModel):
    type: str
    text: str | None = None


class _Response(BaseModel):
    type: Literal["message"]
    role: Literal["assistant"]
    model: ModelName
    content: list[_Block] = Field(max_length=64)
    stop_reason: Literal[
        "end_turn",
        "max_tokens",
        "refusal",
        "tool_use",
        "pause_turn",
        "stop_sequence",
        "model_context_window_exceeded",
    ]
    stop_sequence: str | None = None
    usage: _Usage | None = None


class AnthropicProvider:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        request = ModelRequest.model_validate(request)
        if request.target.provider != "anthropic":
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        model = request.target.model.removeprefix("anthropic/")
        if not model or "/" in model:
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        if self.settings.anthropic_api_key is None:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is required for model-backed research"
            )
        body = _messages(request, model)
        try:
            payload = request_json(
                "anthropic",
                "POST",
                str(self.settings.anthropic_base_url).rstrip("/") + "/messages",
                settings=self.settings.model_copy(
                    update={"request_timeout": request.timeout_seconds}
                ),
                client=self.client,
                precise=True,
                headers={
                    "x-api-key": self.settings.anthropic_api_key.get_secret_value(),
                    "anthropic-version": "2023-06-01",
                    "X-Client-Request-Id": str(request.request_id),
                },
                json={
                    "model": model,
                    "max_tokens": request.max_output_tokens,
                    "stream": False,
                    **body,
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
            counts = response.usage
            inputs = None
            outputs = counts.output_tokens if counts else None
            if counts:
                parts = (
                    counts.input_tokens,
                    counts.cache_creation_input_tokens,
                    counts.cache_read_input_tokens,
                )
                if all(value is not None for value in parts):
                    inputs = sum(parts)
            known = sum(value is not None for value in (inputs, outputs))
            usage = TokenUsage(
                status={0: "unknown", 1: "partial", 2: "known"}[known],
                input_tokens=inputs,
                output_tokens=outputs,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE) from None
        if (
            response.stop_reason in {"tool_use", "pause_turn", "stop_sequence"}
            or response.stop_sequence is not None
            or any(
                block.type != "text" or block.text is None for block in response.content
            )
            or (
                counts
                and counts.server_tool_use
                and any(counts.server_tool_use.values())
            )
        ):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        reason = (
            "refusal"
            if response.stop_reason == "refusal"
            else "length"
            if response.stop_reason in {"max_tokens", "model_context_window_exceeded"}
            else "complete"
        )
        try:
            return ModelReply(
                request_id=request.request_id,
                target=request.target,
                resolved_model=response.model,
                finish_reason=reason,
                content="".join(block.text for block in response.content)
                if reason == "complete"
                else None,
                usage=usage,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage) from None
