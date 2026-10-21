"""Prompt-free, validated model-call provenance for research reports."""

from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from autohedge.ai.config import AgentRole
from autohedge.ai.contracts import (
    ModelName,
    ModelResult,
    ModelTarget,
    OutputTokenLimit,
    Seconds,
    TokenUsage,
)
from autohedge.contracts import Contract, reject_bool


class ModelCallAudit(Contract):
    role: AgentRole
    request_id: UUID
    target: ModelTarget
    resolved_model: ModelName | None = None
    usage: TokenUsage
    max_output_tokens: OutputTokenLimit
    timeout_seconds: Seconds
    started_at: AwareDatetime
    completed_at: AwareDatetime
    elapsed_seconds: Annotated[
        float, BeforeValidator(reject_bool), Field(ge=0, allow_inf_nan=False)
    ]

    @model_validator(mode="after")
    def valid_completion(self):
        if (
            self.completed_at < self.started_at
            or self.elapsed_seconds >= self.timeout_seconds
        ):
            raise ValueError("Invalid model-call timing")
        if (
            self.usage.output_tokens is not None
            and self.usage.output_tokens > self.max_output_tokens
        ):
            raise ValueError("Model-call output limit exceeded")
        return self

    @classmethod
    def from_result(cls, role: AgentRole, result: ModelResult):
        result = ModelResult.model_validate(result)
        return cls(
            role=role,
            request_id=result.request.request_id,
            target=result.request.target,
            resolved_model=result.reply.resolved_model,
            usage=result.reply.usage,
            max_output_tokens=result.request.max_output_tokens,
            timeout_seconds=result.request.timeout_seconds,
            started_at=result.started_at,
            completed_at=result.completed_at,
            elapsed_seconds=result.elapsed_seconds,
        )
