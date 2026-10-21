import copy
import json
import unittest

import httpx
from pydantic import ValidationError

from autohedge.ai import ModelCallError, ModelErrorCode, ModelTarget, complete_model
from autohedge.ai.openai import OpenAIProvider
from autohedge.config import Settings
from model_fixtures import model_request


def completion(**changes):
    return {
        "model": "gpt-4.1-2025-04-14",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Evidence is inconclusive.",
                },
            }
        ],
        "usage": {"prompt_tokens": 80, "completion_tokens": 5},
        **changes,
    }


class OpenAIProviderTests(unittest.TestCase):
    def request(self, **changes):
        return model_request(
            target=ModelTarget(provider="openai", model="gpt-4.1"), **changes
        )

    def test_single_post_honors_identity_limits_endpoint_and_selected_credentials(self):
        received = []

        def respond(request):
            received.append(request)
            return httpx.Response(200, json=completion())

        settings = Settings(
            openai_api_key="fixture-openai-key",
            anthropic_api_key="unrelated-key",
            openai_base_url="https://models.example/v1",
        )
        request = self.request(max_output_tokens=50, timeout_seconds=7)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = complete_model(OpenAIProvider(settings, client=client), request)
        self.assertEqual(len(received), 1)
        sent = received[0]
        self.assertEqual(str(sent.url), "https://models.example/v1/chat/completions")
        self.assertEqual(sent.headers["authorization"], "Bearer fixture-openai-key")
        self.assertEqual(sent.headers["x-client-request-id"], str(request.request_id))
        self.assertEqual(sent.extensions["timeout"]["read"], 7)
        payload = json.loads(sent.content)
        self.assertEqual(payload["max_completion_tokens"], 50)
        self.assertEqual(payload["model"], "gpt-4.1")
        self.assertFalse(payload["stream"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("unrelated-key", sent.content.decode())
        self.assertEqual(result.reply.usage.total_tokens, 85)
        self.assertEqual(result.reply.resolved_model, "gpt-4.1-2025-04-14")

    def test_usage_missing_partial_and_explicit_zero_are_not_confused(self):
        for raw, status, total in (
            (None, "unknown", None),
            ({"prompt_tokens": 10}, "partial", None),
            ({"completion_tokens": 0}, "partial", None),
            ({"prompt_tokens": 0, "completion_tokens": 0}, "known", 0),
        ):
            with (
                self.subTest(raw=raw),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=completion(usage=raw))
                    )
                ) as client,
            ):
                result = complete_model(
                    OpenAIProvider(Settings(openai_api_key="fixture"), client=client),
                    self.request(),
                )
                self.assertEqual(result.reply.usage.status, status)
                self.assertEqual(result.reply.usage.total_tokens, total)

    def test_refusal_truncation_and_content_filter_preserve_billed_usage(self):
        for reason, refusal, code in (
            ("length", None, ModelErrorCode.TRUNCATED),
            ("content_filter", None, ModelErrorCode.REFUSED),
            ("stop", "Not allowed", ModelErrorCode.REFUSED),
        ):
            raw = completion()
            raw["choices"][0]["finish_reason"] = reason
            raw["choices"][0]["message"].update(content=None, refusal=refusal)
            with (
                self.subTest(reason=reason),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=raw)
                    )
                ) as client,
            ):
                with self.assertRaises(ModelCallError) as caught:
                    complete_model(
                        OpenAIProvider(
                            Settings(openai_api_key="fixture"), client=client
                        ),
                        self.request(),
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.usage.total_tokens, 85)

    def test_invalid_responses_never_become_successful_text_or_leak_payloads(self):
        base = completion()
        cases = [
            completion(choices=[]),
            completion(choices=base["choices"] * 2),
            completion(model=" "),
            completion(usage={"prompt_tokens": True}),
            completion(usage={"completion_tokens": 1.5}),
        ]
        for changes in (
            {"index": 1},
            {"finish_reason": "tool_calls"},
            {
                "message": {
                    "role": "assistant",
                    "content": "private-body",
                    "tool_calls": [{"id": "execute_trade"}],
                }
            },
            {"message": {"role": "assistant", "content": " "}},
            {"message": {"role": "user", "content": "private-body"}},
        ):
            raw = copy.deepcopy(base)
            raw["choices"][0].update(changes)
            cases.append(raw)
        for raw in cases:
            with (
                self.subTest(raw=raw),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=raw)
                    )
                ) as client,
            ):
                with self.assertRaises(ModelCallError) as caught:
                    complete_model(
                        OpenAIProvider(
                            Settings(openai_api_key="fixture"), client=client
                        ),
                        self.request(),
                    )
                self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
                self.assertNotIn("private-body", str(caught.exception))

    def test_http_and_network_failures_are_safe_and_never_retried(self):
        for status, code in (
            (401, ModelErrorCode.AUTHENTICATION),
            (403, ModelErrorCode.AUTHENTICATION),
            (429, ModelErrorCode.RATE_LIMITED),
            (503, ModelErrorCode.PROVIDER_FAILURE),
            (404, ModelErrorCode.PROVIDER_FAILURE),
        ):
            calls = []

            def respond(request):
                calls.append(request)
                return httpx.Response(
                    status, text="private-server-body", headers={"Retry-After": "0"}
                )

            with (
                self.subTest(status=status),
                httpx.Client(transport=httpx.MockTransport(respond)) as client,
            ):
                with self.assertRaises(ModelCallError) as caught:
                    complete_model(
                        OpenAIProvider(
                            Settings(openai_api_key="fixture"), client=client
                        ),
                        self.request(),
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private-server-body", str(caught.exception))
                self.assertEqual(len(calls), 1)
        for failure, code in (
            (httpx.ReadTimeout("private-timeout"), ModelErrorCode.TIMEOUT),
            (httpx.ConnectError("private-network"), ModelErrorCode.UNAVAILABLE),
        ):
            calls = []

            def fail(request):
                calls.append(request)
                raise failure

            with (
                self.subTest(code=code),
                httpx.Client(transport=httpx.MockTransport(fail)) as client,
            ):
                with self.assertRaises(ModelCallError) as caught:
                    complete_model(
                        OpenAIProvider(
                            Settings(openai_api_key="fixture"), client=client
                        ),
                        self.request(),
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(len(calls), 1)

    def test_nonfinite_or_duplicate_json_keys_and_other_backends_are_rejected(self):
        for content in (b'{"choices":NaN}', b'{"model":"first","model":"second"}'):
            with httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(200, content=content)
                )
            ) as client:
                with self.assertRaises(ModelCallError) as caught:
                    complete_model(
                        OpenAIProvider(
                            Settings(openai_api_key="fixture"), client=client
                        ),
                        self.request(),
                    )
                self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
        calls = []
        with httpx.Client(
            transport=httpx.MockTransport(lambda req: calls.append(req))
        ) as client:
            provider = OpenAIProvider(Settings(openai_api_key="fixture"), client=client)
            for target in (
                ModelTarget(provider="ollama", model="local"),
                ModelTarget(provider="openai", model="anthropic/claude"),
            ):
                with self.assertRaises(ModelCallError):
                    complete_model(provider, model_request(target=target))
        self.assertEqual(calls, [])

    def test_prefix_normalization_keeps_the_requested_target_and_validates_endpoint(
        self,
    ):
        sent = []
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda req: (
                    sent.append(json.loads(req.content)),
                    httpx.Response(200, json=completion()),
                )[1]
            )
        ) as client:
            request = model_request(
                target=ModelTarget(provider="openai", model="openai/gpt-4.1")
            )
            result = complete_model(
                OpenAIProvider(Settings(openai_api_key="fixture"), client=client),
                request,
            )
        self.assertEqual(sent[0]["model"], "gpt-4.1")
        self.assertEqual(result.reply.target.model, "openai/gpt-4.1")
        for url in (
            "http://models.example",
            "https://user:key@models.example",
            "https://models.example?token=private",
            "https://models.example#fragment",
        ):
            with self.assertRaises(ValidationError):
                Settings(openai_base_url=url)
