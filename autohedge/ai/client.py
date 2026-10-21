"""One validated model call, with no retries, fallback, logging or SDK imports."""

from datetime import datetime, timezone
import math
import time
from typing import Mapping, Protocol

from pydantic import ValidationError

from autohedge.ai.contracts import ModelReply, ModelRequest, ModelResult
from autohedge.ai.errors import ModelCallError, ModelErrorCode
from autohedge.errors import ProviderError


class ModelProvider(Protocol):
    """Adapters honor request limits, normalize replies, and disable SDK retries."""

    def complete(self, request: ModelRequest) -> ModelReply: ...


def utc_now():
    return datetime.now(timezone.utc)


def _wall_clock(clock):
    value = clock()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Clock must return an aware datetime")
    return value.astimezone(timezone.utc)


def _monotonic(clock):
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Monotonic clock must return a finite number")
    if not math.isfinite(value):
        raise ValueError("Monotonic clock must return a finite number")
    return value


def complete_model(
    provider: ModelProvider,
    request: ModelRequest | Mapping[str, object],
    *,
    clock=utc_now,
    monotonic=time.monotonic,
) -> ModelResult:
    """Revalidate both boundaries, correlate identity, and reject unusable replies.

    Synchronous calls cannot be forcibly cancelled here. An adapter must apply
    the request timeout itself; a late return is rejected and never retried.
    """
    try:
        request = ModelRequest.model_validate(request)
    except (ValidationError, ValueError, TypeError):
        raise ModelCallError(ModelErrorCode.INVALID_REQUEST) from None
    try:
        started = _wall_clock(clock)
        before = _monotonic(monotonic)
    except Exception:
        raise ModelCallError(
            ModelErrorCode.INVALID_CLOCK, request_id=request.request_id
        ) from None
    try:
        reply = provider.complete(request)
    except ModelCallError as error:
        try:
            failure = ModelCallError(
                error.code, request_id=request.request_id, usage=error.usage
            )
        except Exception:
            failure = ModelCallError(
                ModelErrorCode.PROVIDER_FAILURE, request_id=request.request_id
            )
        raise failure from None
    except TimeoutError:
        raise ModelCallError(
            ModelErrorCode.TIMEOUT, request_id=request.request_id
        ) from None
    except ConnectionError:
        raise ModelCallError(
            ModelErrorCode.UNAVAILABLE, request_id=request.request_id
        ) from None
    except ProviderError as error:
        status = error.status_code if type(error.status_code) is int else None
        code = {
            401: ModelErrorCode.AUTHENTICATION,
            403: ModelErrorCode.AUTHENTICATION,
            429: ModelErrorCode.RATE_LIMITED,
        }.get(status, ModelErrorCode.PROVIDER_FAILURE)
        raise ModelCallError(code, request_id=request.request_id) from None
    except Exception:
        raise ModelCallError(
            ModelErrorCode.PROVIDER_FAILURE, request_id=request.request_id
        ) from None
    try:
        reply = ModelReply.model_validate(reply)
    except (ValidationError, ValueError, TypeError):
        raise ModelCallError(
            ModelErrorCode.INVALID_RESPONSE, request_id=request.request_id
        ) from None
    if reply.request_id != request.request_id or reply.target != request.target:
        raise ModelCallError(
            ModelErrorCode.RESPONSE_MISMATCH, request_id=request.request_id
        ) from None
    try:
        elapsed = _monotonic(monotonic) - before
        completed = _wall_clock(clock)
        if not math.isfinite(elapsed) or elapsed < 0 or completed < started:
            raise ValueError("Model call clock regressed")
    except Exception:
        raise ModelCallError(
            ModelErrorCode.INVALID_CLOCK,
            request_id=request.request_id,
            usage=reply.usage,
        ) from None
    if elapsed >= request.timeout_seconds:
        raise ModelCallError(
            ModelErrorCode.TIMEOUT, request_id=request.request_id, usage=reply.usage
        ) from None
    if reply.finish_reason != "complete":
        code = {
            "length": ModelErrorCode.TRUNCATED,
            "refusal": ModelErrorCode.REFUSED,
        }[reply.finish_reason]
        raise ModelCallError(
            code, request_id=request.request_id, usage=reply.usage
        ) from None
    if (
        reply.usage.output_tokens is not None
        and reply.usage.output_tokens > request.max_output_tokens
    ):
        raise ModelCallError(
            ModelErrorCode.OUTPUT_LIMIT,
            request_id=request.request_id,
            usage=reply.usage,
        ) from None
    return ModelResult(
        request=request,
        reply=reply,
        started_at=started,
        completed_at=completed,
        elapsed_seconds=elapsed,
    )
