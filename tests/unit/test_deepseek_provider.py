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
from autohedge.ai.deepseek import DeepSeekProvider
from autohedge.config import Settings, load_settings
from autohedge.errors import ConfigurationError
from model_fixtures import model_request


def completion(**changes):
    return {
        "model": "deepseek-chat-fixture",
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
        "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 5,
            "total_tokens": 85,
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
        **changes,
    }


class DeepSeekProviderTests(unittest.TestCase):
    def request(self, **changes):
        return model_request(
            target=ModelTarget(provider="deepseek", model="deepseek/deepseek-chat"),
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
                DeepSeekProvider(
                    settings or Settings(deepseek_api_key="fixture-deepseek"),
                    client=client,
                ),
                request or self.request(),
            )

    def test_native_post_preserves_system_history_limits_and_selected_key(self):
        messages = tuple(
            ModelMessage(role=role, content=text)
            for role, text in (
                ("system", "Trusted research role."),
                ("user", "Evidence A"),
                ("assistant", "Previous thesis"),
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
                deepseek_api_key="fixture-deepseek",
                openai_api_key="private-openai",
                gemini_api_key="private-gemini",
                exa_api_key="private-exa",
                deepseek_base_url="https://models.example/v1",
            ),
        )
        self.assertEqual(len(self.calls), 1)
        sent = self.calls[0]
        self.assertEqual(str(sent.url), "https://models.example/v1/chat/completions")
        self.assertEqual(sent.headers["authorization"], "Bearer fixture-deepseek")
        self.assertEqual(sent.headers["x-client-request-id"], str(request.request_id))
        self.assertNotIn("private-", str(sent.headers) + sent.content.decode())
        self.assertEqual(sent.extensions["timeout"]["read"], 7)
        body = json.loads(sent.content)
        self.assertEqual(
            set(body), {"model", "messages", "max_tokens", "stream", "thinking"}
        )
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["messages"], [m.model_dump() for m in messages])
        self.assertEqual(body["max_tokens"], 50)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(result.reply.usage.input_tokens, 80)
        self.assertEqual(result.reply.usage.total_tokens, 85)
        self.assertEqual(result.reply.target, request.target)
        self.assertEqual(result.reply.resolved_model, "deepseek-chat-fixture")

    def test_usage_retains_unknown_partial_zero_and_exact_total_or_cache_inferences(
        self,
    ):
        for raw, status, inputs, outputs in (
            (None, "unknown", None, None),
            ({}, "unknown", None, None),
            ({"prompt_tokens": 10}, "partial", 10, None),
            ({"completion_tokens": 0}, "partial", None, 0),
            ({"prompt_tokens": 0, "completion_tokens": 0}, "known", 0, 0),
            (
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "prompt_cache_hit_tokens": 7,
                },
                "known",
                10,
                2,
            ),
            (
                {
                    "prompt_cache_hit_tokens": 7,
                    "prompt_cache_miss_tokens": 3,
                    "completion_tokens": 2,
                },
                "known",
                10,
                2,
            ),
            ({"prompt_cache_hit_tokens": 7}, "unknown", None, None),
            ({"total_tokens": 12, "completion_tokens": 2}, "known", 10, 2),
            ({"total_tokens": 12, "prompt_tokens": 10}, "known", 10, 2),
            ({"total_tokens": 12}, "unknown", None, None),
        ):
            with self.subTest(raw=raw):
                usage = self.complete(completion(usage=raw)).reply.usage
                self.assertEqual(
                    (usage.status, usage.input_tokens, usage.output_tokens),
                    (status, inputs, outputs),
                )

    def test_refusals_truncation_and_resource_failures_preserve_usage(self):
        for reason, code in (
            ("content_filter", ModelErrorCode.REFUSED),
            ("length", ModelErrorCode.TRUNCATED),
            ("insufficient_system_resource", ModelErrorCode.UNAVAILABLE),
        ):
            raw = completion()
            raw["choices"][0].update(
                finish_reason=reason, message={"role": "assistant", "content": None}
            )
            with (
                self.subTest(reason=reason),
                self.assertRaises(ModelCallError) as caught,
            ):
                self.complete(raw)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage.total_tokens, 85)
            self.assertEqual(len(self.calls), 1)
        raw = completion()
        raw["choices"][0]["message"]["refusal"] = "private-body"
        with self.assertRaises(ModelCallError) as caught:
            self.complete(raw)
        self.assertEqual(caught.exception.code, ModelErrorCode.REFUSED)

    def test_reasoning_tools_and_unknown_finish_reasons_cannot_be_successful(self):
        cases = []
        for reason in ("tool_calls", "function_call", "new_reason"):
            raw = completion()
            raw["choices"][0]["finish_reason"] = reason
            cases.append(raw)
        for message in (
            {"reasoning_content": "private-body"},
            {"tool_calls": [{"id": "execute_trade"}]},
            {"function_call": {"name": "execute_trade"}},
            {"content": " "},
            {"role": "user"},
        ):
            raw = completion()
            raw["choices"][0]["message"].update(message)
            cases.append(raw)
        raw = completion()
        raw["usage"]["completion_tokens_details"]["reasoning_tokens"] = 1
        cases.append(raw)
        cases.append(completion(error={"message": "private-body"}))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
            self.assertNotIn("private-body", str(caught.exception))

    def test_malformed_schema_json_and_strict_counts_do_not_pass(self):
        cases = [
            completion(choices=[]),
            completion(choices=completion()["choices"] * 2),
            completion(model=" "),
        ]
        raw = completion()
        raw["choices"][0]["index"] = True
        cases.append(raw)
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        ):
            for invalid in (True, -1, 1.5, "10"):
                raw = completion()
                raw["usage"][field] = invalid
                cases.append(raw)
        for invalid in (True, -1, 1.5, "10"):
            raw = completion()
            raw["usage"]["completion_tokens_details"]["reasoning_tokens"] = invalid
            cases.append(raw)
        for raw in cases + [
            httpx.Response(200, text="not JSON"),
            httpx.Response(200, text='{"model":"first","model":"second"}'),
            httpx.Response(200, text='{"usage":{"total_tokens":NaN}}'),
        ]:
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)

    def test_inconsistent_cache_totals_and_reasoning_counts_are_rejected(self):
        for usage in (
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 11},
            {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 8,
                "prompt_cache_miss_tokens": 3,
            },
            {"prompt_tokens": 10, "prompt_cache_hit_tokens": 11},
            {"total_tokens": 1, "completion_tokens": 2},
            {"total_tokens": 1, "prompt_tokens": 2},
            {"total_tokens": 1, "prompt_cache_hit_tokens": 2},
            {
                "completion_tokens": 1,
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
            {"total_tokens": 1, "completion_tokens_details": {"reasoning_tokens": 2}},
        ):
            with self.subTest(usage=usage), self.assertRaises(ModelCallError) as caught:
                self.complete(completion(usage=usage))
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)

    def test_output_limit_applies_to_native_and_inferred_counts_and_content(self):
        for usage in (
            {"completion_tokens": 5},
            {"total_tokens": 15, "prompt_tokens": 10},
        ):
            with self.subTest(usage=usage), self.assertRaises(ModelCallError) as caught:
                self.complete(
                    completion(usage=usage), request=self.request(max_output_tokens=4)
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.OUTPUT_LIMIT)
        raw = completion()
        raw["choices"][0]["message"]["content"] = "a" * 20001
        with self.assertRaises(ModelCallError) as caught:
            self.complete(raw)
        self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
        self.assertEqual(caught.exception.usage.total_tokens, 85)

    def test_http_and_transport_failures_are_safe_and_never_retried(self):
        for raw, code in (
            (httpx.Response(401), ModelErrorCode.AUTHENTICATION),
            (httpx.Response(403), ModelErrorCode.AUTHENTICATION),
            (httpx.Response(429), ModelErrorCode.RATE_LIMITED),
            (httpx.Response(402), ModelErrorCode.PROVIDER_FAILURE),
            (httpx.Response(503), ModelErrorCode.PROVIDER_FAILURE),
            (httpx.ReadTimeout("private-body"), ModelErrorCode.TIMEOUT),
            (httpx.ConnectError("private-body"), ModelErrorCode.UNAVAILABLE),
        ):
            with self.subTest(raw=raw), self.assertRaises(ModelCallError) as caught:
                self.complete(raw)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(self.calls), 1)
            self.assertNotIn("private-body", str(caught.exception))

    def test_wrong_backends_models_and_missing_selected_key_fail_before_http(self):
        targets = [ModelTarget(provider="openai", model="deepseek-chat")]
        targets += [
            ModelTarget(provider="deepseek", model=model)
            for model in (
                "deepseek/",
                "openai/deepseek-chat",
                "deepseek-reasoner",
                "deepseek/deepseek-reasoner",
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
            with self.assertRaisesRegex(ConfigurationError, "DEEPSEEK_API_KEY"):
                DeepSeekProvider(
                    Settings(openai_api_key="unrelated"), client=client
                ).complete(self.request())

    def test_litellm_formatter_cannot_modify_text_roles_or_add_tools(self):
        for messages in (
            [],
            [{"role": "user", "content": "changed"}],
            [{"role": "system", "content": "Analyze frozen evidence."}],
            [
                {
                    "role": "user",
                    "content": "Analyze frozen evidence.",
                    "tools": ["execute_trade"],
                }
            ],
        ):
            with (
                self.subTest(messages=messages),
                patch(
                    "litellm.litellm_core_utils.prompt_templates.common_utils.handle_messages_with_content_list_to_str_conversion",
                    return_value=copy.deepcopy(messages),
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

    def test_endpoint_settings_require_safe_https_and_process_precedence(self):
        self.assertEqual(
            str(Settings().deepseek_base_url), "https://api.deepseek.com/v1"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, ".env")
            path.write_text("AUTOHEDGE_DEEPSEEK_BASE_URL=https://file.example/v1\n")
            settings = load_settings(
                env_file=path,
                environ={"AUTOHEDGE_DEEPSEEK_BASE_URL": "https://process.example/v1"},
            )
            self.assertEqual(
                str(settings.deepseek_base_url), "https://process.example/v1"
            )
            for url in (
                "http://models.example",
                "https://user:private-password@models.example",
                "https://models.example?key=private-key",
                "https://models.example#private-key",
            ):
                with self.subTest(url=url), self.assertRaises(ValidationError):
                    Settings(deepseek_base_url=url)
                with self.assertRaises(ConfigurationError) as caught:
                    load_settings(
                        env_file=path, environ={"AUTOHEDGE_DEEPSEEK_BASE_URL": url}
                    )
                self.assertNotIn("private-", str(caught.exception))
