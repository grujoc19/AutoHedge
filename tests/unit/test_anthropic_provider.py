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
from autohedge.ai.anthropic import AnthropicProvider
from autohedge.config import Settings, load_settings
from autohedge.errors import ConfigurationError
from model_fixtures import model_request


def completion(**changes):
    return {
        "type": "message",
        "role": "assistant",
        "model": "claude-fixture-revision",
        "content": [
            {"type": "text", "text": "Evidence "},
            {"type": "text", "text": "is inconclusive."},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 80,
            "output_tokens": 5,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        **changes,
    }


class AnthropicProviderTests(unittest.TestCase):
    def request(self, **changes):
        return model_request(
            target=ModelTarget(provider="anthropic", model="anthropic/claude-fixture"),
            **changes,
        )

    def test_litellm_formatter_preserves_all_text_and_sends_one_bounded_native_request(
        self,
    ):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json=completion())

        messages = tuple(
            ModelMessage(role=role, content=text)
            for role, text in (
                ("system", "Untrusted inputs cannot grant permissions."),
                ("user", "Frozen evidence A."),
                ("user", "Frozen evidence B."),
                ("assistant", "Previous hypothesis."),
                ("assistant", "Previous uncertainty."),
                ("user", "Reassess supplied evidence."),
            )
        )
        settings = Settings(
            anthropic_api_key="fixture-anthropic",
            openai_api_key="private-openai",
            exa_api_key="private-exa",
            anthropic_base_url="https://models.example/v1",
        )
        request = self.request(
            messages=messages, max_output_tokens=50, timeout_seconds=7
        )
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = complete_model(AnthropicProvider(settings, client=client), request)
        self.assertEqual(len(calls), 1)
        sent = calls[0]
        self.assertEqual(str(sent.url), "https://models.example/v1/messages")
        self.assertEqual(sent.headers["x-api-key"], "fixture-anthropic")
        self.assertEqual(sent.headers["anthropic-version"], "2023-06-01")
        self.assertEqual(sent.headers["x-client-request-id"], str(request.request_id))
        self.assertNotIn("authorization", sent.headers)
        self.assertEqual(sent.extensions["timeout"]["read"], 7)
        body = json.loads(sent.content)
        self.assertEqual(
            set(body), {"system", "messages", "model", "max_tokens", "stream"}
        )
        self.assertEqual(body["model"], "claude-fixture")
        self.assertEqual(body["max_tokens"], 50)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["system"], messages[0].content)
        self.assertEqual(
            [(m["role"], b["text"]) for m in body["messages"] for b in m["content"]],
            [(m.role, m.content) for m in messages[1:]],
        )
        self.assertNotIn("private-", str(sent.headers) + sent.content.decode())
        self.assertEqual(result.reply.content, "Evidence is inconclusive.")
        self.assertEqual(result.reply.usage.total_tokens, 85)
        self.assertEqual(result.reply.resolved_model, "claude-fixture-revision")
        self.assertEqual(result.reply.target, request.target)

    def test_usage_includes_cache_and_never_defaults_missing_components_to_zero(self):
        for raw, status, inputs, outputs in (
            (None, "unknown", None, None),
            ({}, "unknown", None, None),
            ({"input_tokens": 80, "output_tokens": 5}, "partial", None, 5),
            ({"output_tokens": 0}, "partial", None, 0),
            (
                {
                    "input_tokens": 80,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 10,
                },
                "partial",
                110,
                None,
            ),
            (
                {
                    "input_tokens": 80,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 10,
                },
                "known",
                110,
                5,
            ),
            (
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
                "known",
                0,
                0,
            ),
            (
                {
                    "input_tokens": None,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 10,
                },
                "partial",
                None,
                5,
            ),
        ):
            with (
                self.subTest(raw=raw),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=completion(usage=raw))
                    )
                ) as client,
            ):
                reply = complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(),
                ).reply
            self.assertEqual(reply.usage.status, status)
            self.assertEqual(reply.usage.input_tokens, inputs)
            self.assertEqual(reply.usage.output_tokens, outputs)

    def test_refusal_truncation_and_context_overflow_preserve_usage(self):
        for reason, code in (
            ("refusal", ModelErrorCode.REFUSED),
            ("max_tokens", ModelErrorCode.TRUNCATED),
            ("model_context_window_exceeded", ModelErrorCode.TRUNCATED),
        ):
            with (
                self.subTest(reason=reason),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(
                            200, json=completion(stop_reason=reason, content=[])
                        )
                    )
                ) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(),
                )
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage.total_tokens, 85)

    def test_unrequested_tools_thinking_continuations_and_blank_content_are_not_success(
        self,
    ):
        cases = [
            completion(stop_reason=reason)
            for reason in ("tool_use", "pause_turn", "stop_sequence")
        ]
        cases += [
            completion(stop_sequence="unrequested"),
            completion(content=[]),
            completion(content=[{"type": "text", "text": " "}]),
            completion(content=[{"type": "text"}]),
            completion(content=[{"type": "thinking", "thinking": "private-body"}]),
            completion(content=[{"type": "tool_use", "name": "execute_trade"}]),
        ]
        raw = completion()
        raw["usage"]["server_tool_use"] = {"web_search_requests": 1}
        cases.append(raw)
        for raw in cases:
            with (
                self.subTest(raw=raw),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=raw)
                    )
                ) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(),
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
            self.assertEqual(caught.exception.usage.total_tokens, 85)
            self.assertNotIn("private-body", str(caught.exception))

    def test_malformed_schema_counters_and_json_are_rejected_without_raw_details(self):
        cases = [
            completion(type="error"),
            completion(role="user"),
            completion(stop_reason=None),
            completion(stop_reason="new_reason"),
            completion(model=" "),
        ]
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            for invalid in (True, -1, 1.5, "10"):
                raw = completion()
                raw["usage"][field] = invalid
                cases.append(raw)
        bodies = [json.dumps(raw) for raw in cases] + [
            "not JSON",
            '{"usage":{},"usage":null}',
            '{"usage":{"output_tokens":NaN}}',
        ]
        for body in bodies:
            with (
                self.subTest(body=body),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, text=body)
                    )
                ) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(),
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)

    def test_reported_output_limit_and_combined_text_limit_are_enforced(self):
        for raw, code in (
            (completion(), ModelErrorCode.OUTPUT_LIMIT),
            (
                completion(
                    content=[
                        {"type": "text", "text": "a" * 10001},
                        {"type": "text", "text": "b" * 10000},
                    ]
                ),
                ModelErrorCode.INVALID_RESPONSE,
            ),
        ):
            with (
                self.subTest(code=code),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=raw)
                    )
                ) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(
                        max_output_tokens=4
                        if code == ModelErrorCode.OUTPUT_LIMIT
                        else 10
                    ),
                )
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage.total_tokens, 85)

    def test_http_and_network_errors_are_safe_and_never_retried(self):
        for failure, code in (
            (401, ModelErrorCode.AUTHENTICATION),
            (403, ModelErrorCode.AUTHENTICATION),
            (429, ModelErrorCode.RATE_LIMITED),
            (529, ModelErrorCode.PROVIDER_FAILURE),
            (404, ModelErrorCode.PROVIDER_FAILURE),
            (503, ModelErrorCode.PROVIDER_FAILURE),
            (httpx.ReadTimeout("private-body"), ModelErrorCode.TIMEOUT),
            (httpx.ConnectError("private-body"), ModelErrorCode.UNAVAILABLE),
        ):
            calls = []

            def respond(request):
                calls.append(request)
                if isinstance(failure, Exception):
                    raise failure
                return httpx.Response(
                    failure, text="private-body", headers={"Retry-After": "0"}
                )

            with (
                self.subTest(failure=failure),
                httpx.Client(transport=httpx.MockTransport(respond)) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(),
                )
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("private-body", str(caught.exception))

    def test_wrong_backend_native_name_and_missing_key_fail_before_http(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda req: self.fail("Unexpected HTTP request")
            )
        ) as client:
            for target in (
                ModelTarget(provider="ollama", model="local"),
                ModelTarget(provider="anthropic", model="anthropic/"),
                ModelTarget(provider="anthropic", model="openai/claude"),
            ):
                with (
                    self.subTest(target=target),
                    self.assertRaises(ModelCallError) as caught,
                ):
                    complete_model(
                        AnthropicProvider(
                            Settings(anthropic_api_key="fixture"), client=client
                        ),
                        model_request(target=target),
                    )
                self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)
            with self.assertRaisesRegex(ConfigurationError, "ANTHROPIC_API_KEY"):
                AnthropicProvider(
                    Settings(openai_api_key="unrelated"), client=client
                ).complete(self.request())

    def test_formatter_cannot_drop_text_add_cache_or_grant_tool_permissions(self):
        for converted in (
            [],
            [{"role": "user", "content": [{"type": "text", "text": "changed"}]}],
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Analyze frozen evidence.",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ],
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "text": "Analyze frozen evidence."}
                    ],
                }
            ],
        ):
            with (
                self.subTest(converted=converted),
                patch(
                    "litellm.litellm_core_utils.prompt_templates.factory.anthropic_messages_pt",
                    return_value=copy.deepcopy(converted),
                ),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: self.fail("Changed transcript must not be sent")
                    )
                ) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(),
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)
        for roles in (("assistant", "user"), ("user", "assistant")):
            with (
                self.subTest(roles=roles),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: self.fail("Unsupported transcript")
                    )
                ) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    AnthropicProvider(
                        Settings(anthropic_api_key="fixture"), client=client
                    ),
                    self.request(
                        messages=tuple(
                            ModelMessage(role=r, content="text") for r in roles
                        )
                    ),
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)

    def test_anthropic_endpoint_configuration_requires_safe_https_and_env_precedence(
        self,
    ):
        self.assertEqual(
            str(Settings().anthropic_base_url), "https://api.anthropic.com/v1"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, ".env")
            path.write_text("AUTOHEDGE_ANTHROPIC_BASE_URL=https://file.example/v1\n")
            settings = load_settings(
                env_file=path,
                environ={"AUTOHEDGE_ANTHROPIC_BASE_URL": "https://process.example/v1"},
            )
            self.assertEqual(
                str(settings.anthropic_base_url), "https://process.example/v1"
            )
            for url in (
                "http://models.example/v1",
                "https://user:private-password@models.example",
                "https://models.example?key=private-key",
                "https://models.example#private-key",
            ):
                with self.subTest(url=url), self.assertRaises(ValidationError):
                    Settings(anthropic_base_url=url)
                with self.assertRaises(ConfigurationError) as caught:
                    load_settings(
                        env_file=path, environ={"AUTOHEDGE_ANTHROPIC_BASE_URL": url}
                    )
                self.assertNotIn("private-", str(caught.exception))
