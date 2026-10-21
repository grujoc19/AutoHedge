import copy
import json
import unittest
from pathlib import Path
import tempfile

import httpx
from pydantic import ValidationError

from autohedge.ai import ModelCallError, ModelErrorCode, ModelTarget, complete_model
from autohedge.ai.ollama import OllamaProvider
from autohedge.config import Settings, load_settings
from autohedge.errors import ConfigurationError
from model_fixtures import model_request


def completion(**changes):
    return {
        "model": "team/research:7b",
        "message": {"role": "assistant", "content": "Evidence is inconclusive."},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 80,
        "eval_count": 5,
        **changes,
    }


class OllamaProviderTests(unittest.TestCase):
    def request(self, **changes):
        return model_request(
            target=ModelTarget(provider="ollama", model="team/research:7b"), **changes
        )

    def test_native_request_preserves_transcript_limits_and_never_sends_cloud_keys(
        self,
    ):
        received = []

        def respond(request):
            received.append(request)
            return httpx.Response(200, json=completion())

        settings = Settings(
            openai_api_key="private-openai",
            anthropic_api_key="private-anthropic",
            exa_api_key="private-exa",
            ollama_base_url="http://ollama:11434/tenant",
        )
        request = self.request(max_output_tokens=50, timeout_seconds=7)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = complete_model(OllamaProvider(settings, client=client), request)
        self.assertEqual(len(received), 1)
        sent = received[0]
        self.assertEqual(str(sent.url), "http://ollama:11434/tenant/api/chat")
        self.assertEqual(sent.headers["x-client-request-id"], str(request.request_id))
        self.assertEqual(sent.extensions["timeout"]["read"], 7)
        self.assertNotIn("authorization", sent.headers)
        self.assertNotIn("x-api-key", sent.headers)
        for secret in settings.secret_values():
            self.assertNotIn(secret, str(sent.headers) + sent.content.decode())
        payload = json.loads(sent.content)
        self.assertEqual(payload["model"], request.target.model)
        self.assertEqual(
            payload["messages"], [m.model_dump() for m in request.messages]
        )
        self.assertEqual(payload["options"], {"num_predict": 50})
        for option in ("stream", "think", "truncate", "shift"):
            self.assertIs(payload[option], False)
        self.assertNotIn("tools", payload)
        self.assertEqual(result.reply.usage.total_tokens, 85)
        self.assertEqual(result.reply.resolved_model, "team/research:7b")
        self.assertEqual(result.reply.target, request.target)

    def test_usage_missing_partial_and_zero_are_distinct(self):
        for counts, status, total in (
            ({}, "unknown", None),
            ({"eval_count": 0}, "partial", None),
            ({"prompt_eval_count": 10}, "partial", None),
            ({"prompt_eval_count": 0, "eval_count": 0}, "known", 0),
        ):
            raw = completion()
            del raw["prompt_eval_count"], raw["eval_count"]
            raw.update(counts)
            with (
                self.subTest(counts=counts),
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda req: httpx.Response(200, json=raw)
                    )
                ) as client,
            ):
                reply = complete_model(
                    OllamaProvider(Settings(), client=client), self.request()
                ).reply
                self.assertEqual(reply.usage.status, status)
                self.assertEqual(reply.usage.total_tokens, total)

    def test_length_and_reported_output_overrun_fail_with_usage(self):
        for raw, code in (
            (completion(done_reason="length"), ModelErrorCode.TRUNCATED),
            (completion(eval_count=11), ModelErrorCode.OUTPUT_LIMIT),
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
                    OllamaProvider(Settings(), client=client),
                    self.request(max_output_tokens=10),
                )
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.usage.input_tokens, 80)

    def test_incomplete_tool_remote_and_malformed_responses_are_rejected_safely(self):
        cases = [
            completion(done=False),
            completion(done=1),
            completion(done_reason="load"),
            completion(done_reason=None),
            completion(model=" "),
            completion(prompt_eval_count=True),
            completion(eval_count=1.5),
            completion(eval_count=-1),
            completion(remote_host="https://remote.example"),
            completion(remote_model="remote-model"),
            completion(error="private-server-body"),
        ]
        for message in (
            {"role": "user", "content": "private-server-body"},
            {"role": "assistant", "content": " "},
            {"role": "assistant", "thinking": "private-thinking"},
            {
                "role": "assistant",
                "content": "private-server-body",
                "thinking": "private-thinking",
            },
            {"role": "assistant", "content": "", "thinking": "private-thinking"},
            {"role": "assistant", "content": "private-server-body", "tool_calls": [{}]},
        ):
            cases.append(completion(message=message))
        missing_done = copy.deepcopy(completion())
        del missing_done["done"]
        cases.append(missing_done)
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
                    OllamaProvider(Settings(), client=client), self.request()
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)
            self.assertNotIn("private-", str(caught.exception))

    def test_unavailable_timeout_and_http_failures_do_not_retry_or_pull_models(self):
        for failure, code in (
            (httpx.ConnectError("private-server-body"), ModelErrorCode.UNAVAILABLE),
            (httpx.ReadTimeout("private-server-body"), ModelErrorCode.TIMEOUT),
            (401, ModelErrorCode.AUTHENTICATION),
            (403, ModelErrorCode.AUTHENTICATION),
            (429, ModelErrorCode.RATE_LIMITED),
            (404, ModelErrorCode.PROVIDER_FAILURE),
            (503, ModelErrorCode.PROVIDER_FAILURE),
        ):
            calls = []

            def respond(request):
                calls.append(request)
                if isinstance(failure, Exception):
                    raise failure
                return httpx.Response(failure, text="private-server-body")

            with (
                self.subTest(failure=failure),
                httpx.Client(transport=httpx.MockTransport(respond)) as client,
                self.assertRaises(ModelCallError) as caught,
            ):
                complete_model(
                    OllamaProvider(Settings(), client=client), self.request()
                )
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].url.path, "/api/chat")
            self.assertNotIn("private-", str(caught.exception))

    def test_non_json_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for body in ("not JSON", '{"done":true,"done":false}', '{"eval_count":NaN}'):
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
                    OllamaProvider(Settings(), client=client), self.request()
                )
            self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_RESPONSE)

    def test_wrong_backend_is_rejected_before_http(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda req: self.fail("Wrong backend must not make an HTTP call")
            )
        ) as client:
            request = model_request(
                target=ModelTarget(provider="openai", model="gpt-4.1")
            )
            with self.assertRaises(ModelCallError) as caught:
                complete_model(OllamaProvider(Settings(), client=client), request)
        self.assertEqual(caught.exception.code, ModelErrorCode.INVALID_REQUEST)

    def test_endpoint_config_allows_native_http_but_not_embedded_secrets(self):
        self.assertEqual(str(Settings().ollama_base_url), "http://127.0.0.1:11434/")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, ".env")
            path.write_text("AUTOHEDGE_OLLAMA_BASE_URL=http://ollama:11434\n")
            settings = load_settings(
                env_file=path,
                environ={"AUTOHEDGE_OLLAMA_BASE_URL": "https://models.example/local"},
            )
            self.assertEqual(
                str(settings.ollama_base_url), "https://models.example/local"
            )
            for url in (
                "http://user:private-password@localhost:11434",
                "http://localhost:11434?key=private-key",
                "http://localhost:11434#private-key",
                "file:///tmp/model",
            ):
                with self.subTest(url=url), self.assertRaises(ValidationError):
                    Settings(ollama_base_url=url)
                with self.assertRaises(ConfigurationError) as caught:
                    load_settings(
                        env_file=path, environ={"AUTOHEDGE_OLLAMA_BASE_URL": url}
                    )
                self.assertNotIn("private-", str(caught.exception))
