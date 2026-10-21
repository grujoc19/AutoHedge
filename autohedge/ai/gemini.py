"""LiteLLM text conversion and one native Gemini generateContent request."""

from typing import Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
)

from autohedge.ai.config import native_gemini_model
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


class _WirePart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class _WireContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "model"]
    parts: list[_WirePart] = Field(min_length=1, max_length=64)


def _contents(request: ModelRequest) -> dict:
    messages = [m.model_dump() for m in request.messages if m.role != "system"]
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
    try:
        from litellm.llms.vertex_ai.gemini.transformation import (
            _gemini_convert_messages_with_history,
        )

        converted = _gemini_convert_messages_with_history(messages=messages)
        wire = [_WireContent.model_validate(content) for content in converted]
        if [
            ("assistant" if c.role == "model" else c.role, p.text)
            for c in wire
            for p in c.parts
        ] != [(m.role, m.content) for m in request.messages if m.role != "system"]:
            raise ValueError("Message formatter changed the transcript")
    except Exception:
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST) from None
    body = {"contents": [c.model_dump() for c in wire]}
    if request.messages[0].role == "system":
        body["systemInstruction"] = {"parts": [{"text": request.messages[0].content}]}
    return body


class _Usage(BaseModel):
    promptTokenCount: TokenCount | None = None
    candidatesTokenCount: TokenCount | None = None
    thoughtsTokenCount: TokenCount | None = None
    toolUsePromptTokenCount: TokenCount | None = None
    cachedContentTokenCount: TokenCount | None = None
    totalTokenCount: TokenCount | None = None


def _usage(counts: _Usage | None) -> TokenUsage:
    inputs = counts.promptTokenCount if counts else None
    outputs = None
    if counts:
        visible, thoughts = counts.candidatesTokenCount, counts.thoughtsTokenCount
        if counts.totalTokenCount is not None:
            minimum_total = sum(
                n
                for n in (inputs, visible, thoughts, counts.toolUsePromptTokenCount)
                if n is not None
            )
            if minimum_total > counts.totalTokenCount or (
                counts.cachedContentTokenCount is not None
                and counts.cachedContentTokenCount > counts.totalTokenCount
            ):
                raise ValueError("Inconsistent Gemini usage")
        if visible is not None and thoughts is not None:
            outputs = visible + thoughts
        if inputs is not None and counts.totalTokenCount is not None:
            remainder = counts.totalTokenCount - inputs
            minimum = sum(
                n
                for n in (visible, thoughts, counts.toolUsePromptTokenCount)
                if n is not None
            )
            if remainder < minimum:
                raise ValueError("Inconsistent Gemini usage")
            if counts.toolUsePromptTokenCount is not None:
                outputs_from_total = remainder - counts.toolUsePromptTokenCount
                if outputs is not None and outputs != outputs_from_total:
                    raise ValueError("Inconsistent Gemini usage")
                outputs = outputs_from_total
            elif visible is not None and remainder == visible:
                outputs = visible  # Nonnegative missing components must both be zero.
            elif outputs is not None and outputs != remainder:
                raise ValueError("Unexplained Gemini usage")
        if inputs is not None:
            if (
                counts.cachedContentTokenCount is not None
                and counts.cachedContentTokenCount > inputs
            ):
                raise ValueError("Inconsistent Gemini cache usage")
            if counts.toolUsePromptTokenCount is not None:
                inputs += counts.toolUsePromptTokenCount
    known = sum(n is not None for n in (inputs, outputs))
    return TokenUsage(
        status={0: "unknown", 1: "partial", 2: "known"}[known],
        input_tokens=inputs,
        output_tokens=outputs,
    )


class _Part(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str | None = None
    thought: StrictBool | None = None
    thoughtSignature: str | None = None


class _Content(BaseModel):
    role: Literal["model"]
    parts: list[_Part] = Field(max_length=64)


class _Rating(BaseModel):
    blocked: StrictBool | None = None


class _Candidate(BaseModel):
    index: StrictInt = 0
    content: _Content | None = None
    finishReason: str
    safetyRatings: list[_Rating] = Field(default_factory=list)
    groundingMetadata: dict | None = None
    urlContextMetadata: dict | None = None


class _Feedback(BaseModel):
    blockReason: str | None = None
    safetyRatings: list[_Rating] = Field(default_factory=list)


class _Response(BaseModel):
    modelVersion: ModelName | None = None
    candidates: list[_Candidate] = Field(default_factory=list, max_length=1)
    promptFeedback: _Feedback | None = None
    usageMetadata: _Usage | None = None
    error: dict | None = None


class GeminiProvider:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        request = ModelRequest.model_validate(request)
        if request.target.provider != "gemini":
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        try:
            model = native_gemini_model(request.target.model)
        except ValueError:
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST) from None
        if self.settings.gemini_api_key is None:
            raise ConfigurationError(
                "GEMINI_API_KEY is required for model-backed research"
            )
        body = _contents(request)
        try:
            payload = request_json(
                "gemini",
                "POST",
                str(self.settings.gemini_base_url).rstrip("/")
                + "/models/"
                + model
                + ":generateContent",
                settings=self.settings.model_copy(
                    update={"request_timeout": request.timeout_seconds}
                ),
                client=self.client,
                precise=True,
                headers={
                    "x-goog-api-key": self.settings.gemini_api_key.get_secret_value(),
                    "X-Client-Request-Id": str(request.request_id),
                },
                json={
                    **body,
                    "generationConfig": {
                        "candidateCount": 1,
                        "maxOutputTokens": request.max_output_tokens,
                        "responseMimeType": "text/plain",
                        "thinkingConfig": {
                            "thinkingBudget": 0,
                            "includeThoughts": False,
                        },
                    },
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
            usage = _usage(response.usageMetadata)
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE) from None
        counts = response.usageMetadata
        if (
            counts
            and counts.candidatesTokenCount is not None
            and counts.candidatesTokenCount > request.max_output_tokens
        ):
            raise ModelCallError(ModelErrorCode.OUTPUT_LIMIT, usage=usage)
        if response.error is not None or (
            counts
            and (
                counts.thoughtsTokenCount
                or counts.toolUsePromptTokenCount
                or (
                    counts.promptTokenCount is not None
                    and counts.totalTokenCount is not None
                    and counts.candidatesTokenCount is not None
                    and counts.totalTokenCount
                    > counts.promptTokenCount + counts.candidatesTokenCount
                )
                or (
                    usage.output_tokens is not None
                    and counts.candidatesTokenCount is not None
                    and usage.output_tokens > counts.candidatesTokenCount
                )
            )
        ):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        reason = "complete"
        feedback = response.promptFeedback
        if feedback and (
            feedback.blockReason
            not in {None, "BLOCK_REASON_UNSPECIFIED", "BLOCKED_REASON_UNSPECIFIED"}
            or any(r.blocked for r in feedback.safetyRatings)
        ):
            reason = "refusal"
        elif not response.candidates:
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        if response.candidates:
            candidate = response.candidates[0]
            if (
                candidate.index != 0
                or candidate.groundingMetadata
                or candidate.urlContextMetadata
            ):
                raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
            parts = candidate.content.parts if candidate.content else []
            if any(p.text is None or p.thought or p.model_extra for p in parts):
                raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
            if candidate.finishReason in {
                "SAFETY",
                "RECITATION",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
                "SPII",
                "IMAGE_SAFETY",
                "IMAGE_PROHIBITED_CONTENT",
            } or any(r.blocked for r in candidate.safetyRatings):
                reason = "refusal"
            elif candidate.finishReason in {"MAX_TOKENS", "CONTINUATION"}:
                reason = "length" if reason == "complete" else reason
            elif candidate.finishReason != "STOP":
                raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage)
        else:
            parts = []
        try:
            return ModelReply(
                request_id=request.request_id,
                target=request.target,
                resolved_model=response.modelVersion,
                finish_reason=reason,
                content="".join(p.text for p in parts)
                if reason == "complete"
                else None,
                usage=usage,
            )
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE, usage=usage) from None
