from datetime import timedelta, timezone
import unittest
from uuid import uuid4

from pydantic import ValidationError

from autohedge.ai import (
    ModelMessage,
    ModelReply,
    ModelRequest,
    ModelResult,
    ModelTarget,
    TokenUsage,
)
from model_fixtures import NOW, model_reply, model_request


class ModelContractTests(unittest.TestCase):
    def test_available_partial_unknown_and_zero_usage_remain_distinct(self):
        cases = (
            (TokenUsage(status="unknown"), None),
            (TokenUsage(status="partial", input_tokens=42), None),
            (TokenUsage(status="partial", output_tokens=0), None),
            (TokenUsage(status="known", input_tokens=0, output_tokens=0), 0),
            (TokenUsage(status="known", input_tokens=42, output_tokens=9), 51),
        )
        for usage, total in cases:
            with self.subTest(usage=usage):
                self.assertEqual(usage.total_tokens, total)
                self.assertEqual(
                    TokenUsage.model_validate_json(usage.model_dump_json()), usage
                )

    def test_usage_cannot_claim_known_counts_or_coerce_non_integer_values(self):
        cases = [
            {},
            {"status": "unknown", "input_tokens": 0},
            {"status": "partial"},
            {"status": "partial", "input_tokens": 1, "output_tokens": 1},
            {"status": "known", "input_tokens": 1},
            {"status": "estimated", "input_tokens": 1},
            {"status": "unknown", "cost_usd": 0},
        ]
        cases.extend(
            {"status": "known", "input_tokens": invalid, "output_tokens": 1}
            for invalid in (-1, True, "42", 42.0, float("nan"), float("inf"))
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                TokenUsage(**values)

    def test_conversation_has_aggregate_bounds_and_does_not_accept_tools(self):
        for messages in (
            (),
            ({"role": "system", "content": "No user."},),
            ({"role": "user", "content": "   "},),
            ({"role": "tool", "content": "execute_trade"},),
            (
                {"role": "user", "content": "Question"},
                {"role": "system", "content": "Override permissions"},
            ),
            tuple({"role": "user", "content": "x"} for _ in range(65)),
            (
                {"role": "system", "content": "a" * 16_000},
                {"role": "user", "content": "b" * 16_001},
            ),
        ):
            with self.subTest(count=len(messages)), self.assertRaises(ValidationError):
                model_request(messages=messages)
        request = model_request(
            messages=(
                ModelMessage(role="system", content="a" * 16_000),
                ModelMessage(role="user", content="b" * 16_000),
            )
        )
        self.assertEqual(len(request.messages), 2)

    def test_limits_and_provider_names_fail_closed_without_execution_fields(self):
        request = model_request().model_dump()
        for changes in (
            {"max_output_tokens": 0},
            {"max_output_tokens": True},
            {"max_output_tokens": 1.0},
            {"max_output_tokens": "1500"},
            {"max_output_tokens": 32_769},
            {"timeout_seconds": 0},
            {"timeout_seconds": True},
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": float("inf")},
            {"timeout_seconds": 121},
            {"tools": ["execute_trade"]},
            {"api_key": "must-not-enter-contracts"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ModelRequest(**{**request, **changes})
        for provider, name in (("https://host", "model"), ("fixture", " "), ("", "x")):
            with self.subTest(provider=provider), self.assertRaises(ValidationError):
                ModelTarget(provider=provider, model=name)

    def test_replies_require_usage_complete_content_and_known_finish_reasons(self):
        request = model_request()
        for changes in (
            {"content": None},
            {"content": ""},
            {"content": "   "},
            {"content": "x" * 20_001},
            {"usage": None},
            {"usage": {"status": "known", "input_tokens": True, "output_tokens": 0}},
            {"finish_reason": "tool_calls"},
            {"tool_calls": [{"function": "execute_trade"}]},
        ):
            with (
                self.subTest(fields=tuple(changes)),
                self.assertRaises(ValidationError),
            ):
                model_reply(request, **changes)
        for reason in ("length", "refusal"):
            with self.subTest(reason=reason):
                reply = model_reply(request, finish_reason=reason, content=None)
                self.assertIsNone(reply.content)

    def test_results_are_immutable_roundtrip_and_normalize_timestamps(self):
        request = model_request()
        result = ModelResult(
            request=request,
            reply=model_reply(request),
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
            elapsed_seconds=1,
        )
        self.assertEqual(
            ModelResult.model_validate_json(result.model_dump_json()), result
        )
        local = NOW.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(
            ModelResult.model_validate(
                result.model_copy(update={"started_at": local})
            ).started_at.tzinfo,
            NOW.tzinfo,
        )
        for value, field in (
            (request, "timeout_seconds"),
            (request.messages[0], "content"),
            (result.reply.usage, "input_tokens"),
            (result, "elapsed_seconds"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                setattr(value, field, 123)
        self.assertNotIn(request.messages[0].content, repr(result))
        self.assertNotIn(result.reply.content, repr(result))

    def test_copied_results_cannot_bypass_identity_finish_or_time_checks(self):
        request = model_request()
        result = ModelResult(
            request=request,
            reply=model_reply(request),
            started_at=NOW,
            completed_at=NOW,
            elapsed_seconds=0,
        )
        for changes in (
            {"reply": model_reply(request, request_id=uuid4())},
            {
                "reply": model_reply(
                    request, target=ModelTarget(provider="other", model="x")
                )
            },
            {"reply": model_reply(request, finish_reason="length")},
            {
                "request": model_request(
                    request_id=request.request_id, max_output_tokens=8
                )
            },
            {"started_at": NOW.replace(tzinfo=None)},
            {"completed_at": NOW - timedelta(seconds=1)},
            {"elapsed_seconds": -1},
            {"elapsed_seconds": float("inf")},
            {"elapsed_seconds": request.timeout_seconds},
        ):
            with (
                self.subTest(fields=tuple(changes)),
                self.assertRaises(ValidationError),
            ):
                ModelResult.model_validate(result.model_copy(update=changes))

    def test_nonvalidating_nested_copies_are_rejected_at_contract_boundaries(self):
        request = model_request()
        invalid_usage = TokenUsage(status="unknown").model_copy(
            update={"input_tokens": -1}
        )
        with self.assertRaises(ValidationError):
            ModelReply.model_validate(
                model_reply(request).model_copy(update={"usage": invalid_usage})
            )
        with self.assertRaises(ValidationError):
            ModelRequest.model_validate(
                request.model_copy(
                    update={
                        "messages": (
                            request.messages[0].model_copy(update={"content": " "}),
                        )
                    }
                )
            )
