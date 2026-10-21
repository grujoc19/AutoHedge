"""Immutable text-completion contracts, independent of credentials and SDKs."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BeforeValidator, Field, StrictInt, model_validator

from autohedge.contracts import Contract, reject_bool

TokenCount = Annotated[StrictInt, Field(ge=0)]
OutputTokenLimit = Annotated[StrictInt, Field(ge=1, le=32_768)]
Seconds = Annotated[
    float, BeforeValidator(reject_bool), Field(gt=0, le=120, allow_inf_nan=False)
]
ModelName = Annotated[str, Field(min_length=1, max_length=200)]


class ModelTarget(Contract):
    provider: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    model: ModelName


class ModelMessage(Contract):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=32_000, repr=False)]


class ModelRequest(Contract):
    request_id: UUID = Field(default_factory=uuid4)
    target: ModelTarget
    messages: Annotated[
        tuple[ModelMessage, ...], Field(min_length=1, max_length=64, repr=False)
    ]
    max_output_tokens: OutputTokenLimit = 1500
    timeout_seconds: Seconds = 30

    @model_validator(mode="after")
    def bounded_conversation(self):
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("A model request requires a user message")
        if any(message.role == "system" for message in self.messages[1:]):
            raise ValueError("Only the first message may have the system role")
        if sum(len(message.content) for message in self.messages) > 32_000:
            raise ValueError("Model request exceeds its total character budget")
        return self


class TokenUsage(Contract):
    """Unknown/partial counts are never fabricated as zero or billed estimates."""

    status: Literal["known", "partial", "unknown"]
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None

    @model_validator(mode="after")
    def explicit_availability(self):
        count = sum(
            value is not None for value in (self.input_tokens, self.output_tokens)
        )
        if count != {"known": 2, "partial": 1, "unknown": 0}[self.status]:
            raise ValueError("Token counts do not match their availability status")
        return self

    @property
    def total_tokens(self) -> int | None:
        if self.status != "known":
            return None
        return self.input_tokens + self.output_tokens


class ModelReply(Contract):
    """Adapter-normalized reply; only a complete reply can become a ModelResult."""

    request_id: UUID
    target: ModelTarget
    resolved_model: ModelName | None = None
    finish_reason: Literal["complete", "length", "refusal"]
    content: Annotated[
        str | None, Field(min_length=1, max_length=20_000, repr=False)
    ] = None
    usage: TokenUsage

    @model_validator(mode="after")
    def complete_content(self):
        if self.finish_reason == "complete" and self.content is None:
            raise ValueError("A complete reply requires nonblank content")
        return self


class ModelResult(Contract):
    request: ModelRequest
    reply: ModelReply
    started_at: AwareDatetime
    completed_at: AwareDatetime
    elapsed_seconds: Annotated[
        float, BeforeValidator(reject_bool), Field(ge=0, allow_inf_nan=False)
    ]

    @model_validator(mode="after")
    def usable_correlated_reply(self):
        if (
            self.reply.request_id != self.request.request_id
            or self.reply.target != self.request.target
        ):
            raise ValueError("Reply does not match its model request")
        if self.reply.finish_reason != "complete":
            raise ValueError("Incomplete model replies are not usable results")
        if (
            self.reply.usage.output_tokens is not None
            and self.reply.usage.output_tokens > self.request.max_output_tokens
        ):
            raise ValueError("Reported output tokens exceeded the request limit")
        if self.completed_at < self.started_at:
            raise ValueError("Model call completed before it started")
        if self.elapsed_seconds >= self.request.timeout_seconds:
            raise ValueError("Model result exceeded its call deadline")
        return self
