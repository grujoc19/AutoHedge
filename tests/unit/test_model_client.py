from datetime import timedelta
import traceback
import unittest
from uuid import uuid4

from autohedge.ai import (
    ModelCallError,
    ModelErrorCode,
    ModelRequest,
    ModelTarget,
    TokenUsage,
    complete_model,
)
from autohedge.errors import AutoHedgeError, ProviderError
from model_fixtures import FakeModelProvider, NOW, model_reply, model_request


class ModelClientTests(unittest.TestCase):
    def call(self, provider, request=None, *, wall=None, ticks=None):
        dates = iter(wall if wall is not None else (NOW, NOW + timedelta(seconds=1)))
        times = iter(ticks if ticks is not None else (10.0, 11.0))
        return complete_model(
            provider,
            request if request is not None else model_request(),
            clock=lambda: next(dates),
            monotonic=lambda: next(times),
        )

    def test_single_successful_call_records_identity_usage_and_timing(self):
        provider = FakeModelProvider()
        request = model_request()
        result = self.call(provider, request)
        self.assertEqual(provider.requests, [request])
        self.assertEqual(result.request.request_id, request.request_id)
        self.assertEqual(result.reply.target, request.target)
        self.assertEqual(result.reply.resolved_model, "local-test-v1-revision2")
        self.assertEqual(result.reply.usage.total_tokens, 51)
        self.assertEqual(result.started_at, NOW)
        self.assertEqual(result.completed_at, NOW + timedelta(seconds=1))
        self.assertEqual(result.elapsed_seconds, 1)

    def test_no_openai_specific_assumption_and_unknown_usage_stays_unknown(self):
        for name in ("openai", "anthropic", "gemini", "deepseek", "ollama", "custom"):
            request = model_request(
                target=ModelTarget(provider=name, model="test-model")
            )
            provider = FakeModelProvider(
                lambda req: model_reply(req, usage=TokenUsage(status="unknown"))
            )
            with self.subTest(provider=name):
                result = self.call(provider, request)
                self.assertEqual(result.reply.target.provider, name)
                self.assertIsNone(result.reply.usage.total_tokens)
                self.assertEqual(len(provider.requests), 1)

    def test_invalid_request_never_calls_the_provider(self):
        for request in (
            {"api_key": "private-invalid-input"},
            model_request().model_copy(update={"timeout_seconds": True}),
            model_request().model_copy(update={"max_output_tokens": -1}),
        ):
            provider = FakeModelProvider()
            with (
                self.subTest(kind=type(request)),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider, request)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)
            self.assertEqual(provider.requests, [])
            self.assertNotIn("private-invalid-input", str(caught.exception))

    def test_invalid_or_forged_reply_is_not_a_result_and_does_not_leak_payload(self):
        secret = "private-content-from-provider"
        request = model_request()
        for reply in (
            "not a normalized reply: " + secret,
            {**model_reply(request).model_dump(), "content": " ", "key": secret},
            model_reply(request).model_copy(update={"usage": {"status": "known"}}),
        ):
            provider = FakeModelProvider(lambda req: reply)
            with (
                self.subTest(kind=type(reply)),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider, request)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
            self.assertEqual(caught.exception.usage.status, "unknown")
            self.assertNotIn(secret, str(caught.exception))
            self.assertEqual(len(provider.requests), 1)

    def test_reply_for_another_request_or_target_is_rejected(self):
        request = model_request()
        for changes in (
            {"request_id": uuid4()},
            {"target": ModelTarget(provider="other", model=request.target.model)},
            {"target": ModelTarget(provider=request.target.provider, model="other")},
        ):
            provider = FakeModelProvider(lambda req: model_reply(req, **changes))
            with (
                self.subTest(fields=tuple(changes)),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider, request)
            self.assertEqual(caught.exception.code, ModelErrorCode.RESPONSE_MISMATCH)
            self.assertEqual(caught.exception.request_id, request.request_id)
            self.assertEqual(caught.exception.usage.status, "unknown")

    def test_timeout_and_provider_errors_are_safe_and_never_retried(self):
        secret = "sensitive-body-or-key"
        cases = (
            (TimeoutError(secret), ModelErrorCode.TIMEOUT),
            (ConnectionError(secret), ModelErrorCode.UNAVAILABLE),
            (RuntimeError(secret), ModelErrorCode.PROVIDER_FAILURE),
            (ProviderError(secret, secret, 401), ModelErrorCode.AUTHENTICATION),
            (ProviderError(secret, secret, 403), ModelErrorCode.AUTHENTICATION),
            (ProviderError(secret, secret, 429), ModelErrorCode.RATE_LIMITED),
            (ProviderError(secret, secret, 503), ModelErrorCode.PROVIDER_FAILURE),
            (
                ProviderError(secret, secret, {"invalid": secret}),
                ModelErrorCode.PROVIDER_FAILURE,
            ),
        )
        for failure, expected in cases:
            provider = FakeModelProvider(error=failure)
            request = model_request()
            with (
                self.subTest(expected=expected),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider, request)
            error = caught.exception
            self.assertIsInstance(error, AutoHedgeError)
            self.assertEqual(error.code, expected)
            self.assertEqual(error.request_id, request.request_id)
            self.assertEqual(error.usage.status, "unknown")
            self.assertEqual(len(provider.requests), 1)
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, "".join(traceback.format_exception(error)))

    def test_explicit_adapter_failure_preserves_usage_but_owns_the_request_id(self):
        usage = TokenUsage(status="partial", output_tokens=9)
        failure = ModelCallError(
            ModelErrorCode.UNAVAILABLE, request_id=uuid4(), usage=usage
        )
        request = model_request()
        provider = FakeModelProvider(error=failure)
        with self.assertRaises(ModelCallError) as caught:
            self.call(provider, request)
        self.assertEqual(caught.exception.request_id, request.request_id)
        self.assertEqual(caught.exception.usage, usage)
        self.assertEqual(len(provider.requests), 1)

    def test_truncation_and_refusal_preserve_usage_without_exposing_content(self):
        for reason, code in (
            ("length", ModelErrorCode.TRUNCATED),
            ("refusal", ModelErrorCode.REFUSED),
        ):
            provider = FakeModelProvider(
                lambda req: model_reply(req, finish_reason=reason)
            )
            with (
                self.subTest(reason=reason),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage.total_tokens, 51)
            self.assertEqual(len(provider.requests), 1)

    def test_malformed_adapter_failure_is_normalized_without_raw_validation_errors(
        self,
    ):
        failure = ModelCallError(ModelErrorCode.UNAVAILABLE)
        failure.usage = {"status": "known", "input_tokens": "private-invalid-usage"}
        missing = ModelCallError(ModelErrorCode.UNAVAILABLE)
        del missing.usage
        for invalid in (failure, missing):
            provider = FakeModelProvider(error=invalid)
            with (
                self.subTest(error=invalid),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider)
            self.assertEqual(caught.exception.code, ModelErrorCode.PROVIDER_FAILURE)
            self.assertEqual(caught.exception.usage.status, "unknown")
            self.assertNotIn("private-invalid-usage", str(caught.exception))
            self.assertEqual(len(provider.requests), 1)

    def test_late_return_is_rejected_with_usage_and_never_retried(self):
        request = model_request(timeout_seconds=1)
        provider = FakeModelProvider()
        with self.assertRaises(ModelCallError) as caught:
            self.call(provider, request, ticks=(10, 11))
        self.assertEqual(caught.exception.code, ModelErrorCode.TIMEOUT)
        self.assertEqual(caught.exception.usage.total_tokens, 51)
        self.assertEqual(len(provider.requests), 1)

    def test_reported_output_over_limit_is_rejected_and_not_hidden_from_accounting(
        self,
    ):
        for usage in (
            TokenUsage(status="known", input_tokens=42, output_tokens=9),
            TokenUsage(status="partial", output_tokens=9),
        ):
            provider = FakeModelProvider(lambda req: model_reply(req, usage=usage))
            with self.subTest(usage=usage), self.assertRaises(ModelCallError) as caught:
                self.call(provider, model_request(max_output_tokens=8))
            self.assertEqual(caught.exception.code, ModelErrorCode.OUTPUT_LIMIT)
            self.assertEqual(caught.exception.usage, usage)
            self.assertEqual(len(provider.requests), 1)

    def test_invalid_initial_clocks_prevent_calls_and_regression_rejects_results(self):
        for wall, ticks in (
            ((NOW.replace(tzinfo=None),), (10,)),
            ((NOW,), (True,)),
            ((NOW,), (float("inf"),)),
            ((NOW,), ("10",)),
        ):
            provider = FakeModelProvider()
            with (
                self.subTest(wall=wall, ticks=ticks),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider, wall=wall, ticks=ticks)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_CLOCK)
            self.assertEqual(provider.requests, [])
        for wall, ticks in (
            ((NOW, NOW - timedelta(seconds=1)), (10, 11)),
            ((NOW, NOW), (10, 9)),
            ((NOW, NOW), (10, float("nan"))),
            ((NOW, NOW), (-1e308, 1e308)),
            ((NOW, NOW.replace(tzinfo=None)), (10, 11)),
        ):
            provider = FakeModelProvider()
            with (
                self.subTest(wall=wall, ticks=ticks),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.call(provider, wall=wall, ticks=ticks)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_CLOCK)
            self.assertEqual(caught.exception.usage.total_tokens, 51)
            self.assertEqual(len(provider.requests), 1)

    def test_serialized_inputs_are_validated_by_the_same_boundary(self):
        request = model_request()
        provider = FakeModelProvider(
            lambda req: model_reply(req).model_dump(mode="json")
        )
        result = self.call(provider, request.model_dump(mode="json"))
        self.assertIsInstance(result.request, ModelRequest)
        self.assertEqual(result.request, request)
