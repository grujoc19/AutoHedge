import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from autohedge.ai import (
    ModelCallError,
    ModelErrorCode,
    ModelMessage,
    ModelTarget,
    complete_model,
)
from autohedge.ai.gemini import GeminiProvider
from autohedge.config import Settings, load_settings
from autohedge.errors import ConfigurationError
from model_fixtures import model_request


def completion(**changes):
    return {
        "modelVersion": "gemini-fixture-revision",
        "candidates": [
            {
                "index": 0,
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [{"text": "Evidence "}, {"text": "is inconclusive."}],
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 80,
            "candidatesTokenCount": 5,
            "totalTokenCount": 85,
        },
        **changes,
    }


class GeminiProviderTests(unittest.TestCase):
    def request(self, **changes):
        return model_request(
            target=ModelTarget(provider="gemini", model="gemini/gemini-fixture"),
            **changes,
        )

    def complete(self, raw, *, request=None, settings=None):
        self.calls = []

        def respond(sent):
            self.calls.append(sent)
            if isinstance(raw, Exception):
                raise raw
            return (
                raw
                if isinstance(raw, httpx.Response)
                else httpx.Response(200, json=raw)
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            return complete_model(
                GeminiProvider(
                    settings or Settings(gemini_api_key="fixture-gemini"), client=client
                ),
                request or self.request(),
            )

    def test_litellm_text_conversion_preserves_system_history_limits_and_selected_key(
        self,
    ):
        messages = tuple(
            ModelMessage(role=role, content=text)
            for role, text in (
                ("system", "Trusted research role."),
                ("user", "Evidence A"),
                ("user", "Evidence B"),
                ("assistant", "Previous thesis"),
                ("assistant", "Uncertainty"),
                ("user", "Reassess evidence"),
            )
        )
        request = self.request(
            messages=messages, max_output_tokens=50, timeout_seconds=7
        )
        result = self.complete(
            completion(),
            request=request,
            settings=Settings(
                gemini_api_key="fixture-gemini",
                openai_api_key="private-openai",
                anthropic_api_key="private-anthropic",
                exa_api_key="private-exa",
                gemini_base_url="https://models.example/v1beta",
            ),
        )
        self.assertEqual(len(self.calls), 1)
        sent = self.calls[0]
        self.assertEqual(
            str(sent.url),
            "https://models.example/v1beta/models/gemini-fixture:generateContent",
        )
        self.assertFalse(sent.url.query)
        self.assertEqual(sent.headers["x-goog-api-key"], "fixture-gemini")
        self.assertEqual(sent.headers["x-client-request-id"], str(request.request_id))
        self.assertNotIn("authorization", sent.headers)
        self.assertNotIn("x-api-key", sent.headers)
        self.assertNotIn("private-", str(sent.headers) + sent.content.decode())
        self.assertEqual(sent.extensions["timeout"]["read"], 7)
        body = json.loads(sent.content)
        self.assertEqual(
            set(body), {"contents", "systemInstruction", "generationConfig"}
        )
        self.assertEqual(
            body["systemInstruction"], {"parts": [{"text": messages[0].content}]}
        )
        self.assertEqual(
            body["generationConfig"],
            {
                "candidateCount": 1,
                "maxOutputTokens": 50,
                "responseMimeType": "text/plain",
                "thinkingConfig": {"thinkingBudget": 0, "includeThoughts": False},
            },
        )
        self.assertEqual(
            [
                ("assistant" if c["role"] == "model" else c["role"], p["text"])
                for c in body["contents"]
                for p in c["parts"]
            ],
            [(m.role, m.content) for m in messages[1:]],
        )
        self.assertEqual(result.reply.content, "Evidence is inconclusive.")
        self.assertEqual(result.reply.target, request.target)
        self.assertEqual(result.reply.resolved_model, "gemini-fixture-revision")
        self.assertEqual(result.reply.usage.total_tokens, 85)

    def test_usage_only_infers_missing_parts_when_totals_prove_them(self):
        for raw, status, inputs, outputs in (
            (None, "unknown", None, None),
            ({}, "unknown", None, None),
            ({"promptTokenCount": 10, "candidatesTokenCount": 2}, "partial", 10, None),
            ({"candidatesTokenCount": 2, "thoughtsTokenCount": 0}, "partial", None, 2),
            (
                {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 12,
                },
                "known",
                10,
                2,
            ),
            (
                {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "cachedContentTokenCount": 8,
                    "thoughtsTokenCount": 0,
                },
                "known",
                10,
                2,
            ),
            (
                {
                    "promptTokenCount": 10,
                    "totalTokenCount": 12,
                    "toolUsePromptTokenCount": 0,
                },
                "known",
                10,
                2,
            ),
            (
                {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": None,
                },
                "partial",
                10,
                None,
            ),
            (
                {
                    "promptTokenCount": 0,
                    "candidatesTokenCount": 0,
                    "totalTokenCount": 0,
                },
                "known",
                0,
                0,
            ),
        ):
            with self.subTest(raw=raw):
                usage = self.complete(completion(usageMetadata=raw)).reply.usage
                self.assertEqual(
                    (usage.status, usage.input_tokens, usage.output_tokens),
                    (status, inputs, outputs),
                )

    def test_blocked_prompt_and_candidates_refuse_without_followup_requests(self):
        for default in ("BLOCK_REASON_UNSPECIFIED", "BLOCKED_REASON_UNSPECIFIED"):
            self.complete(completion(promptFeedback={"blockReason": default}))
        cases = [completion(candidates=[], promptFeedback={"blockReason": "SAFETY"})]
        for reason in (
            "SAFETY",
            "RECITATION",
            "BLOCKLIST",
            "PROHIBITED_CONTENT",
            "SPII",
            "IMAGE_SAFETY",
        ):
            raw = completion()
            raw["candidates"][0].update(finishReason=reason, content=None)
            cases.append(raw)
        raw = completion()
        raw["candidates"][0]["safetyRatings"] = [{"blocked": True}]
        cases.append(raw)
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, ModelErrorCode.REFUSED)
            self.assertEqual(caught.exception.usage.total_tokens, 85)
            self.assertEqual(len(self.calls), 1)

    def test_limits_truncation_and_unknown_finish_reasons_never_become_success(self):
        for reason, code in (
            ("MAX_TOKENS", ModelErrorCode.TRUNCATED),
            ("CONTINUATION", ModelErrorCode.TRUNCATED),
            ("MALFORMED_FUNCTION_CALL", ModelErrorCode.INVALID_RESPONSE),
            ("UNEXPECTED_TOOL_CALL", ModelErrorCode.INVALID_RESPONSE),
            ("OTHER", ModelErrorCode.INVALID_RESPONSE),
            ("NEW_REASON", ModelErrorCode.INVALID_RESPONSE),
            ("", ModelErrorCode.INVALID_RESPONSE),
        ):
            raw = completion()
            raw["candidates"][0]["finishReason"] = reason
            with (
                self.subTest(reason=reason),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.complete(raw)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage.total_tokens, 85)
        with self.assertRaises(ModelCallError) as caught:
            self.complete(completion(), request=self.request(max_output_tokens=4))
        self.assertEqual(caught.exception.code, ModelErrorCode.OUTPUT_LIMIT)

        with self.assertRaises(ModelCallError) as caught:
            self.complete(
                completion(usageMetadata={"candidatesTokenCount": 5}),
                request=self.request(max_output_tokens=4),
            )
        self.assertEqual(caught.exception.code, ModelErrorCode.OUTPUT_LIMIT)

    def test_thinking_tool_calls_grounding_and_contradictory_counters_are_rejected(
        self,
    ):
        cases = []
        for part in (
            {"functionCall": {"name": "execute_trade"}},
            {"text": "private-body", "thought": True},
            {"text": "private-body", "inlineData": {"data": "opaque"}},
            {"text": " "},
        ):
            raw = completion()
            raw["candidates"][0]["content"]["parts"] = [part]
            cases.append(raw)
        for field in ("groundingMetadata", "urlContextMetadata"):
            raw = completion()
            raw["candidates"][0][field] = {"unrequested": "private-body"}
            cases.append(raw)
        for counts in (
            {"candidatesTokenCount": 5, "thoughtsTokenCount": 0, "totalTokenCount": 4},
            {"cachedContentTokenCount": 5, "totalTokenCount": 4},
            {"promptTokenCount": 10, "candidatesTokenCount": 2, "totalTokenCount": 15},
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 2,
                "thoughtsTokenCount": 1,
                "totalTokenCount": 13,
            },
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 2,
                "toolUsePromptTokenCount": 1,
                "totalTokenCount": 13,
            },
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 2,
                "thoughtsTokenCount": 0,
                "totalTokenCount": 11,
            },
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 2,
                "cachedContentTokenCount": 11,
            },
            {"promptTokenCount": 10, "candidatesTokenCount": 2, "totalTokenCount": 9},
            {
                "promptTokenCount": 10,
                "candidatesTokenCount": 2,
                "thoughtsTokenCount": 0,
                "toolUsePromptTokenCount": 0,
                "totalTokenCount": 13,
            },
        ):
            cases.append(completion(usageMetadata=counts))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
            self.assertNotIn("private-body", str(caught.exception))

    def test_malformed_json_schema_and_counters_fail_safely(self):
        cases = [
            completion(candidates=[]),
            completion(candidates=completion()["candidates"] * 2),
            completion(modelVersion=" "),
            completion(error={"message": "private-body"}),
        ]
        for field in (
            "promptTokenCount",
            "candidatesTokenCount",
            "thoughtsTokenCount",
            "toolUsePromptTokenCount",
            "cachedContentTokenCount",
            "totalTokenCount",
        ):
            for invalid in (True, -1, 1.5, "10"):
                cases.append(completion(usageMetadata={field: invalid}))
        for update in (
            {"index": 1},
            {"index": True},
            {"finishReason": None},
            {"content": {"role": "user", "parts": [{"text": "private-body"}]}},
        ):
            raw = completion()
            raw["candidates"][0].update(update)
            cases.append(raw)
        for raw in cases + [
            httpx.Response(200, text="not JSON"),
            httpx.Response(200, text='{"candidates":[],"candidates":[]}'),
            httpx.Response(200, text='{"usageMetadata":{"totalTokenCount":NaN}}'),
        ]:
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
            self.assertNotIn("private-body", str(caught.exception))

    def test_unreported_model_version_stays_unknown_and_text_has_one_combined_limit(
        self,
    ):
        self.assertIsNone(
            self.complete(completion(modelVersion=None)).reply.resolved_model
        )
        raw = completion()
        del raw["candidates"][0]["index"]
        self.complete(raw)  # Proto JSON may omit the zero index.
        raw["candidates"][0]["content"]["parts"] = [
            {"text": "a" * 10001},
            {"text": "b" * 10000},
        ]
        with self.assertRaises(ModelCallError) as caught:
            self.complete(raw)
        self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
        self.assertEqual(caught.exception.usage.total_tokens, 85)

    def test_http_and_transport_errors_are_not_retried_and_do_not_leak(self):
        for raw, code in (
            (httpx.Response(401), ModelErrorCode.AUTHENTICATION),
            (httpx.Response(403), ModelErrorCode.AUTHENTICATION),
            (httpx.Response(429), ModelErrorCode.RATE_LIMITED),
            (httpx.Response(503), ModelErrorCode.PROVIDER_FAILURE),
            (httpx.Response(400, text="private-body"), ModelErrorCode.PROVIDER_FAILURE),
            (httpx.ReadTimeout("private-body"), ModelErrorCode.TIMEOUT),
            (httpx.ConnectError("private-body"), ModelErrorCode.UNAVAILABLE),
        ):
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(self.calls), 1)
            self.assertNotIn("private-body", str(caught.exception))

    def test_wrong_backend_url_injection_and_missing_key_fail_before_http(self):
        targets = [ModelTarget(provider="openai", model="gemini-fixture")]
        targets += [
            ModelTarget(provider="gemini", model=model)
            for model in (
                "gemini/",
                "models/gemini-fixture",
                "openai/gemini-fixture",
                "../other",
                "gemini-fixture?key=x",
                "gemini-fixture#fragment",
                "gemini-fixture:streamGenerateContent",
                "https://other.example",
                "bad model",
                "gemini%2Fother",
            )
        ]
        for target in targets:
            with (
                self.subTest(target=target),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.complete(completion(), request=model_request(target=target))
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)
            self.assertEqual(self.calls, [])
        with httpx.Client(
            transport=httpx.MockTransport(lambda req: self.fail("Missing selected key"))
        ) as client:
            with self.assertRaisesRegex(ConfigurationError, "GEMINI_API_KEY"):
                GeminiProvider(
                    Settings(openai_api_key="unrelated"), client=client
                ).complete(self.request())

    def test_formatter_cannot_drop_text_add_tools_or_promote_instructions(self):
        for converted in (
            [],
            [{"role": "user", "parts": [{"text": "changed"}]}],
            [
                {
                    "role": "user",
                    "parts": [{"text": "Analyze frozen evidence.", "functionCall": {}}],
                }
            ],
            [{"role": "system", "parts": [{"text": "Analyze frozen evidence."}]}],
        ):
            with (
                self.subTest(converted=converted),
                patch(
                    "litellm.llms.vertex_ai.gemini.transformation._gemini_convert_messages_with_history",
                    return_value=copy.deepcopy(converted),
                ),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.complete(completion())
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)
            self.assertEqual(self.calls, [])
        for roles in (("assistant", "user"), ("user", "assistant")):
            with self.subTest(roles=roles), self.assertRaises(ModelCallError):
                self.complete(
                    completion(),
                    request=self.request(
                        messages=tuple(
                            ModelMessage(role=r, content="text") for r in roles
                        )
                    ),
                )
            self.assertEqual(self.calls, [])

    def test_endpoint_config_requires_https_and_respects_process_precedence(self):
        self.assertEqual(
            str(Settings().gemini_base_url),
            "https://generativelanguage.googleapis.com/v1beta",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, ".env")
            path.write_text("AUTOHEDGE_GEMINI_BASE_URL=https://file.example/v1beta\n")
            settings = load_settings(
                env_file=path,
                environ={"AUTOHEDGE_GEMINI_BASE_URL": "https://process.example/v1beta"},
            )
            self.assertEqual(
                str(settings.gemini_base_url), "https://process.example/v1beta"
            )
            for url in (
                "http://models.example",
                "https://user:private-password@models.example",
                "https://models.example?key=private-key",
                "https://models.example#private-key",
            ):
                with self.subTest(url=url), self.assertRaises(ValidationError):
                    Settings(gemini_base_url=url)
                with self.assertRaises(ConfigurationError) as caught:
                    load_settings(
                        env_file=path, environ={"AUTOHEDGE_GEMINI_BASE_URL": url}
                    )
                self.assertNotIn("private-", str(caught.exception))
