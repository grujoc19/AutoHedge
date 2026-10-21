from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest

from loguru import logger
from pydantic import SecretStr, ValidationError

from autohedge.ai import (
    AgentRole,
    ModelMessage,
    ModelRequest,
    ModelTarget,
    RoleModelConfig,
    RoleModelOverride,
    RoleModels,
)
from autohedge.config import Settings, load_settings
from autohedge.errors import ConfigurationError
from autohedge.logging import configure_logging


def profile(provider="openai", model="test-model", **limits):
    return {"target": {"provider": provider, "model": model}, **limits}


class RoleConfigurationTests(unittest.TestCase):
    def load(self, environ, contents=""):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / ".env"
            file.write_text(contents, encoding="utf-8")
            return load_settings(environ=environ, env_file=file)

    def test_all_six_defaults_preserve_legacy_models_and_inherit_timeout(self):
        settings = Settings(
            model="legacy-main", sentiment_model="legacy-news", request_timeout=12
        )
        self.assertEqual(len(AgentRole), 6)
        for role in AgentRole:
            with self.subTest(role=role):
                resolved = settings.model_for(role)
                self.assertEqual(resolved.target.provider, "openai")
                self.assertEqual(
                    resolved.target.model,
                    "legacy-news"
                    if role == AgentRole.NEWS_INTELLIGENCE
                    else "legacy-main",
                )
                self.assertEqual(resolved.timeout_seconds, 12)
                self.assertEqual(resolved.max_output_tokens, 1500)
        settings.require_legacy_model_configuration()

    def test_independent_role_profiles_form_ready_to_use_model_requests(self):
        providers = ("ollama", "ollama", "ollama", "deepseek", "anthropic", "gemini")
        profiles = {
            role.value: profile(
                provider,
                model=f"model-{index}",
                max_output_tokens=200 + index,
                timeout_seconds=5 + index,
            )
            for index, (role, provider) in enumerate(zip(AgentRole, providers))
        }
        settings = Settings(role_models=profiles)
        for index, (role, provider) in enumerate(zip(AgentRole, providers)):
            request = ModelRequest(
                messages=(ModelMessage(role="user", content="Frozen evidence"),),
                **settings.model_for(role).model_dump(),
            )
            with self.subTest(role=role):
                self.assertEqual(request.target.provider, provider)
                self.assertEqual(request.target.model, f"model-{index}")
                self.assertEqual(request.max_output_tokens, 200 + index)
                self.assertEqual(request.timeout_seconds, 5 + index)
        self.assertEqual(settings.model, "gpt-4.1")

    def test_per_role_timeout_override_and_global_inheritance(self):
        settings = Settings(
            request_timeout=8,
            role_models={
                "quant": profile("ollama", "local-quant"),
                "risk_manager": profile(
                    "openai", "risk-model", timeout_seconds=3, max_output_tokens=250
                ),
            },
        )
        self.assertEqual(settings.model_for("quant").timeout_seconds, 8)
        self.assertEqual(settings.model_for("risk_manager").timeout_seconds, 3)
        changed = settings.model_copy(
            update={"request_timeout": 15, "model": "new-main"}
        )
        self.assertEqual(changed.model_for("quant").timeout_seconds, 15)
        self.assertEqual(changed.model_for("quant").target.model, "local-quant")
        self.assertEqual(changed.model_for("trading_director").target.model, "new-main")

    def test_role_and_limit_overrides_load_from_both_sources_without_mutation(self):
        before = dict(os.environ)
        settings = self.load(
            {
                "AUTOHEDGE_NEWS_INTELLIGENCE_MODEL": "process-news",
                "AUTOHEDGE_NEWS_INTELLIGENCE_MAX_OUTPUT_TOKENS": "256",
                "AUTOHEDGE_MODEL": "process-main",
                "AUTOHEDGE_REQUEST_TIMEOUT": "12",
            },
            """AUTOHEDGE_NEWS_INTELLIGENCE_PROVIDER=ollama
AUTOHEDGE_NEWS_INTELLIGENCE_MODEL=file-news
AUTOHEDGE_NEWS_INTELLIGENCE_TIMEOUT=7
AUTOHEDGE_MODEL=file-main
AUTOHEDGE_SENTIMENT_MODEL=legacy-sentiment
AUTOHEDGE_QUANT_MODEL=explicit-file-quant
""",
        )
        news = settings.model_for("news_intelligence")
        self.assertEqual(
            news.target, ModelTarget(provider="ollama", model="process-news")
        )
        self.assertEqual(news.max_output_tokens, 256)
        self.assertEqual(news.timeout_seconds, 7)
        self.assertEqual(
            settings.model_for("quant").target.model, "explicit-file-quant"
        )
        self.assertEqual(settings.model_for("quant").target.provider, "openai")
        self.assertEqual(settings.model_for("quant").timeout_seconds, 12)
        self.assertEqual(
            settings.model_for("risk_manager").target.model, "process-main"
        )
        self.assertEqual(settings.sentiment_model, "legacy-sentiment")
        self.assertEqual(dict(os.environ), before)

    def test_each_role_environment_prefix_is_wired_to_its_own_profile(self):
        for role in AgentRole:
            prefix = f"AUTOHEDGE_{role.value.upper()}_"
            settings = self.load(
                {
                    prefix + "PROVIDER": "ollama",
                    prefix + "MODEL": "fixture-local",
                    prefix + "MAX_OUTPUT_TOKENS": "200",
                    prefix + "TIMEOUT": "6",
                }
            )
            with self.subTest(role=role):
                resolved = settings.model_for(role)
                self.assertEqual(resolved.target.provider, "ollama")
                self.assertEqual(resolved.max_output_tokens, 200)
                self.assertEqual(resolved.timeout_seconds, 6)
                for other in AgentRole:
                    if other != role:
                        self.assertEqual(
                            settings.model_for(other).target.provider, "openai"
                        )

    def test_empty_and_malformed_profiles_fail_safely_instead_of_falling_back(self):
        cases = (
            {"AUTOHEDGE_QUANT_PROVIDER": "ollama"},
            {"AUTOHEDGE_QUANT_MODEL": ""},
            {"AUTOHEDGE_QUANT_MODEL": " "},
            {"AUTOHEDGE_QUANT_MODEL": "x", "AUTOHEDGE_QUANT_PROVIDER": ""},
            {
                "AUTOHEDGE_QUANT_MODEL": "x",
                "AUTOHEDGE_QUANT_PROVIDER": "private-invalid-provider",
            },
            {"AUTOHEDGE_QUANT_MODEL": "x", "AUTOHEDGE_QUANT_MAX_OUTPUT_TOKENS": "1.5"},
            {"AUTOHEDGE_QUANT_MODEL": "x", "AUTOHEDGE_QUANT_MAX_OUTPUT_TOKENS": "true"},
            {"AUTOHEDGE_QUANT_MODEL": "x", "AUTOHEDGE_QUANT_MAX_OUTPUT_TOKENS": "0"},
            {
                "AUTOHEDGE_QUANT_MODEL": "x",
                "AUTOHEDGE_QUANT_MAX_OUTPUT_TOKENS": "32769",
            },
            {"AUTOHEDGE_QUANT_MODEL": "x", "AUTOHEDGE_QUANT_TIMEOUT": "nan"},
            {"AUTOHEDGE_QUANT_MODEL": "x", "AUTOHEDGE_QUANT_TIMEOUT": "121"},
            {"AUTOHEDGE_QUANT_TIMEOUT": "5"},
        )
        for environ in cases:
            with (
                self.subTest(environ=environ),
                self.assertRaises(ConfigurationError) as caught,
            ):
                self.load(environ, "AUTOHEDGE_MODEL=usable-legacy-model\n")
            self.assertNotIn("private-invalid-provider", str(caught.exception))
        with self.assertRaises(ConfigurationError):
            self.load({}, "AUTOHEDGE_QUANT_MODEL=x\nAUTOHEDGE_QUANT_TIMEOUT\n")

    def test_role_environment_typos_do_not_silently_select_a_default_model(self):
        for name in (
            "AUTOHEDGE_NEWS_INTELLIGENC_MODEL",
            "AUTOHEDGE_QUANT_TIMEOUT_SECONDS",
            "AUTOHEDGE_SENTIMENT_PROVIDER",
            "AUTOHEDGE_RISK_MANAGER_MODLE",
        ):
            with (
                self.subTest(name=name),
                self.assertRaises(ConfigurationError) as caught,
            ):
                self.load({name: "private-invalid-value"})
            self.assertNotIn("private-invalid-value", str(caught.exception))

    def test_invalid_copied_profiles_and_unknown_roles_cannot_escape_validation(self):
        settings = Settings(role_models={"quant": profile("ollama")})
        override = settings.role_models.quant.model_copy(
            update={"max_output_tokens": True}
        )
        forged = settings.model_copy(
            update={
                "role_models": settings.role_models.model_copy(
                    update={"quant": override}
                )
            }
        )
        with self.assertRaises(ConfigurationError):
            forged.model_for("quant")
        with self.assertRaises(ConfigurationError):
            forged.require_legacy_model_configuration()
        with self.assertRaises(ConfigurationError) as caught:
            settings.model_for("private-unknown-role")
        self.assertNotIn("private-unknown-role", str(caught.exception))

    def test_profiles_and_resolved_results_are_immutable_and_roundtrip(self):
        settings = Settings(role_models={"quant": profile("ollama", "local")})
        resolved = settings.model_for("quant")
        for value, field, change in (
            (settings.role_models, "quant", None),
            (settings.role_models.quant, "max_output_tokens", 2),
            (resolved.target, "model", "changed"),
            (resolved, "timeout_seconds", 1),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                setattr(value, field, change)
        self.assertEqual(
            RoleModels.model_validate_json(settings.role_models.model_dump_json()),
            settings.role_models,
        )
        self.assertEqual(
            RoleModelConfig.model_validate_json(resolved.model_dump_json()), resolved
        )
        with self.assertRaises(ValidationError):
            RoleModelConfig(target=resolved.target)

    def test_direct_configuration_rejects_unsupported_backends_and_unbounded_limits(
        self,
    ):
        for values in (
            profile("unsupported", "x"),
            profile("ollama", "x", max_output_tokens=True),
            profile("ollama", "x", max_output_tokens="200"),
            profile("ollama", "x", max_output_tokens=0),
            profile("ollama", "x", timeout_seconds=True),
            profile("ollama", "x", timeout_seconds=float("inf")),
            {**profile(), "api_key": "not-a-profile-secret"},
        ):
            with self.subTest(fields=tuple(values)), self.assertRaises(ValidationError):
                RoleModelOverride(**values)
        with self.assertRaises(ValidationError):
            RoleModels(unknown_role=profile())
        with self.assertRaises(ValidationError):
            Settings(request_timeout=True)

    def test_local_model_needs_no_cloud_key_and_never_returns_an_unrelated_key(self):
        settings = Settings(
            openai_api_key="unrelated-openai",
            role_models={"news_intelligence": profile("ollama", "local")},
        )
        settings.require_model_credentials("news_intelligence")
        self.assertIsNone(settings.model_api_key("news_intelligence"))
        self.assertIsNotNone(settings.model_api_key("quant"))

    def test_cloud_credentials_are_selected_per_backend_without_fallback(self):
        for backend in ("openai", "anthropic", "gemini", "deepseek"):
            field = f"{backend}_api_key"
            settings = Settings(
                role_models={"quant": profile(backend)}, **{field: "selected-secret"}
            )
            with self.subTest(backend=backend):
                settings.require_model_credentials("quant")
                self.assertEqual(
                    settings.model_api_key("quant").get_secret_value(),
                    "selected-secret",
                )
                missing = settings.model_copy(update={field: None})
                with self.assertRaisesRegex(
                    ConfigurationError, backend.upper() + "_API_KEY"
                ):
                    missing.require_model_credentials("quant")

    def test_provider_keys_are_hidden_serialized_excluded_and_redacted(self):
        secrets = {
            f"{backend}_api_key": f"private-{backend}-value"
            for backend in ("openai", "anthropic", "gemini", "deepseek")
        }
        settings = Settings(**secrets)
        output = StringIO()
        configure_logging(settings, sink=output)
        logger.info(" ".join(secrets.values()))
        for secret in secrets.values():
            self.assertNotIn(secret, repr(settings))
            self.assertNotIn(secret, settings.model_dump_json())
            self.assertNotIn(secret, output.getvalue())
            self.assertIn(secret, settings.secret_values())

    def test_key_alias_precedence_clearing_and_wrapped_blank_secrets(self):
        settings = self.load(
            {
                "GOOGLE_API_KEY": "process-gemini",
                "ANTHROPIC_API_KEY": "process-anthropic",
                "DEEPSEEK_API_KEY": "process-deepseek",
            },
            "GEMINI_API_KEY=file-gemini\nANTHROPIC_API_KEY=file-anthropic\nDEEPSEEK_API_KEY=file-deepseek\n",
        )
        self.assertEqual(settings.gemini_api_key.get_secret_value(), "process-gemini")
        self.assertEqual(
            settings.anthropic_api_key.get_secret_value(), "process-anthropic"
        )
        self.assertEqual(
            settings.deepseek_api_key.get_secret_value(), "process-deepseek"
        )
        cleared = self.load(
            {
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "must-not-win",
                "ANTHROPIC_API_KEY": "",
                "DEEPSEEK_API_KEY": "",
            },
            "GEMINI_API_KEY=file-gemini\nANTHROPIC_API_KEY=file-anthropic\nDEEPSEEK_API_KEY=file-deepseek\n",
        )
        self.assertIsNone(cleared.gemini_api_key)
        self.assertIsNone(cleared.anthropic_api_key)
        self.assertIsNone(cleared.deepseek_api_key)
        self.assertIsNone(
            Settings(anthropic_api_key=SecretStr("   ")).anthropic_api_key
        )

    def test_active_overrides_are_rejected_by_legacy_research_even_with_valid_keys(
        self,
    ):
        for role in ("quant", "trading_director", "news_intelligence", "risk_manager"):
            settings = Settings(
                openai_api_key="fixture-key",
                exa_api_key="fixture-exa",
                role_models={role: profile("ollama", "local")},
            )
            settings.require_model_credentials(role)
            for configured in (
                settings,
                settings.model_copy(update={"search_enabled": False}),
            ):
                with (
                    self.subTest(role=role),
                    self.assertRaisesRegex(ConfigurationError, "Role-specific"),
                ):
                    configured.require_research_credentials()

    def test_inactive_future_roles_do_not_require_credentials_for_legacy_research(self):
        settings = Settings(
            openai_api_key="fixture-key",
            exa_api_key="fixture-exa",
            role_models={
                "market_anomaly": profile("anthropic", "future-anomaly"),
                "on_chain_intelligence": profile("ollama", "optional-interpretation"),
            },
        )
        settings.require_research_credentials()
