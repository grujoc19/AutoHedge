from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from loguru import logger
from pydantic import ValidationError

from autohedge.config import Settings, load_settings
from autohedge.errors import ConfigurationError
from autohedge.logging import _patch, configure_logging


class ConfigurationTests(unittest.TestCase):
    def test_environment_precedence_aliases_and_no_process_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            file.write_text(
                "OPENAI_API_KEY=file-key\nAUTOHEDGE_MODEL=file-model\nJUPITER_API_KEY=optional-key\n"
            )
            environ = {
                "AUTOHEDGE_OPENAI_API_KEY": "process-key",
                "AUTOHEDGE_MODEL": "process-model",
            }
            before = dict(os.environ)
            settings = load_settings(environ=environ, env_file=file)
            self.assertEqual(settings.model, "process-model")
            self.assertEqual(settings.openai_api_key.get_secret_value(), "process-key")
            self.assertEqual(dict(os.environ), before)
            self.assertNotIn("process-key", repr(settings))
            self.assertNotIn("process-key", settings.model_dump_json())
            cleared = load_settings(environ={"OPENAI_API_KEY": ""}, env_file=file)
            self.assertIsNone(cleared.openai_api_key)

    def test_optional_coingecko_settings_auth_and_secret_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            file.write_text("COINGECKO_PRO_API_KEY=file-history-secret\n")
            settings = load_settings(
                environ={
                    "COINGECKO_PRO_API_KEY": "process-history-secret",
                    "AUTOHEDGE_COINGECKO_BASE_URL": "https://history.example/api/v3",
                },
                env_file=file,
            )
            self.assertEqual(
                settings.headers("coingecko"),
                {"x-cg-pro-api-key": "process-history-secret"},
            )
            self.assertIn("process-history-secret", settings.secret_values())
            self.assertNotIn("process-history-secret", repr(settings))
            self.assertNotIn("process-history-secret", settings.model_dump_json())
            self.assertIsNone(
                load_settings(
                    environ={"COINGECKO_PRO_API_KEY": ""}, env_file=file
                ).coingecko_api_key
            )
        with self.assertRaises(ValidationError):
            Settings(coingecko_base_url="http://history.example")

    def test_optional_providers_are_not_required_for_research(self):
        Settings(
            openai_api_key="test-openai-key", exa_api_key="test-exa-key"
        ).require_research_credentials()
        Settings(
            openai_api_key="test-openai-key", search_enabled=False
        ).require_research_credentials()
        with self.assertRaisesRegex(ConfigurationError, "EXA_API_KEY"):
            Settings(openai_api_key="test-openai-key").require_research_credentials()

    def test_invalid_config_has_safe_error_and_bounded_timeouts(self):
        for value in ("0", "-1", "nan", "inf", "121", "secret-invalid-value"):
            with (
                self.subTest(value=value),
                self.assertRaises(ConfigurationError) as error,
            ):
                load_settings(environ={"AUTOHEDGE_REQUEST_TIMEOUT": value})
            self.assertNotIn(value, str(error.exception))
        for url in (
            "http://example.com",
            "https://user:password@example.com",
            "https://example.com?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                Settings(exa_base_url=url)

    def test_legacy_massive_url_and_canonical_env_precedence(self):
        settings = load_settings(
            environ={
                "POLYGON_BASE_URL": "https://old.example",
                "MASSIVE_BASE_URL": "https://new.example",
            }
        )
        self.assertEqual(str(settings.massive_base_url), "https://new.example/")


class LoggingTests(unittest.TestCase):
    def test_dependency_message_dumps_do_not_persist_prompt_or_agent_state(self):
        record = dict(
            name="swarms.structs.agent",
            message="Task: private-task; Agent State: private-state",
            extra={"run_id": "sample", "stage": "risk", "payload": "private-payload"},
            exception=RuntimeError("private-error"),
            level=SimpleNamespace(name="ERROR"),
        )
        _patch(record)
        self.assertNotIn("private-", str(record))
        self.assertEqual(record["extra"], {"run_id": "sample", "stage": "risk"})
        self.assertIsNone(record["exception"])

    def test_secrets_headers_context_and_tracebacks_are_safe(self):
        output = StringIO()

        def capture(message):
            output.write(str(message) + json.dumps(message.record["extra"]))

        configure_logging(
            Settings(
                openai_api_key="known-openai-secret",
                solana_private_key="known-wallet-secret",
                coingecko_api_key="known-history-secret",
            ),
            sink=capture,
        )
        with logger.contextualize(run_id="test-run", stage="quant"):
            logger.bind(
                payload={
                    "Authorization": "Bearer unknown-secret",
                    "nested": "known-wallet-secret",
                }
            ).info(
                "key known-openai-secret; Authorization: Bearer unknown-secret; x-api-key=other-secret; x-cg-pro-api-key=unconfigured-cg-value; known-history-secret"
            )
            try:
                raise RuntimeError("known-wallet-secret")
            except RuntimeError:
                logger.exception("Safe failure category")
        text = output.getvalue()
        for secret in (
            "known-openai-secret",
            "known-wallet-secret",
            "unknown-secret",
            "other-secret",
            "known-history-secret",
            "unconfigured-cg-value",
        ):
            self.assertNotIn(secret, text)
        self.assertIn("test-run", text)
        self.assertIn("quant", text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("Traceback", text)

    def test_reconfiguration_replaces_implicit_sinks_without_duplicates(self):
        output, implicit = StringIO(), StringIO()
        logger.add(implicit, format="{message}")
        configure_logging(Settings(), sink=output)
        configure_logging(Settings(), sink=output)
        logger.info("one entry")
        self.assertEqual(output.getvalue().count("one entry"), 1)
        self.assertEqual(implicit.getvalue(), "")
