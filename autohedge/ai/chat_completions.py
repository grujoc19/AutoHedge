"""Shared native Chat Completions schema and text-only reply validation."""

from typing import Literal

from pydantic import BaseModel, Field, StrictInt, ValidationError

from autohedge.ai.contracts import (
    ModelName,
    ModelReply,
    ModelRequest,
    TokenCount,
    TokenUsage,
)
from autohedge.ai.errors import ModelCallError, ModelErrorCode


class ChatUsage(BaseModel):
    prompt_tokens: TokenCount | None = None
    completion_tokens: TokenCount | None = None


class ChatMessage(BaseModel):
    role: Literal["assistant"]
    content: str | None = None
    refusal: str | None = None
    tool_calls: list | None = None
    function_call: dict | None = None


class ChatChoice(BaseModel):
    index: StrictInt
    finish_reason: Literal[
        "stop", "length", "content_filter", "tool_calls", "function_call"
    ]
    message: ChatMessage


class ChatResponse(BaseModel):
    model: ModelName
    choices: list[ChatChoice] = Field(min_length=1, max_length=1)
    usage: ChatUsage | None = None


def token_usage(inputs: int | None, outputs: int | None) -> TokenUsage:
    count = sum(value is not None for value in (inputs, outputs))
    return TokenUsage(
        status={0: "unknown", 1: "partial", 2: "known"}[count],
        input_tokens=inputs,
        output_tokens=outputs,
    )


def reply_from_response(
    request: ModelRequest, response: ChatResponse, usage: TokenUsage
) -> ModelReply:
    choice = response.choices[0]
    if (
        choice.index != 0
        or choice.message.tool_calls
        or choice.message.function_call
        or choice.finish_reason not in {"stop", "length", "content_filter"}
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
