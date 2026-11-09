"""One OpenAI Chat Completions POST through the existing safe HTTP transport."""

import httpx
from pydantic import ValidationError

from autohedge.ai.chat_completions import ChatResponse, reply_from_response, token_usage
from autohedge.ai.contracts import ModelReply, ModelRequest
from autohedge.ai.errors import ModelCallError, ModelErrorCode
from autohedge.config import Settings
from autohedge.errors import ConfigurationError, ProviderError
from autohedge.providers import request_json


class OpenAIProvider:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def complete(self, request: ModelRequest) -> ModelReply:
        request = ModelRequest.model_validate(request)
        if request.target.provider != "openai":
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        model = request.target.model.removeprefix("openai/")
        if not model or "/" in model:
            raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
        if self.settings.openai_api_key is None:
            raise ConfigurationError(
                "OPENAI_API_KEY is required for model-backed research"
            )
        try:
            payload = request_json(
                "openai",
                "POST",
                str(self.settings.openai_base_url).rstrip("/") + "/chat/completions",
                settings=self.settings.model_copy(
                    update={"request_timeout": request.timeout_seconds}
                ),
                client=self.client,
                precise=True,
                headers={
                    "Authorization": "Bearer "
                    + self.settings.openai_api_key.get_secret_value(),
                    "X-Client-Request-Id": str(request.request_id),
                },
                json={
                    "model": model,
                    "messages": [message.model_dump() for message in request.messages],
                    "max_completion_tokens": request.max_output_tokens,
                    "stream": False,
                    "n": 1,
                },
            )
        except ProviderError as error:
            code = {
                401: ModelErrorCode.AUTHENTICATION,
                403: ModelErrorCode.AUTHENTICATION,
                429: ModelErrorCode.RATE_LIMITED,
            }.get(
                error.status_code,
                {
                    "request timed out": ModelErrorCode.TIMEOUT,
                    "connection failed": ModelErrorCode.UNAVAILABLE,
                    "invalid JSON response": ModelErrorCode.INVALID_RESPONSE,
                }.get(error.reason, ModelErrorCode.PROVIDER_FAILURE),
            )
            raise ModelCallError(code) from None
        try:
            response = ChatResponse.model_validate(payload)
            inputs = response.usage.prompt_tokens if response.usage else None
            outputs = response.usage.completion_tokens if response.usage else None
            usage = token_usage(inputs, outputs)
        except (ValidationError, ValueError, TypeError):
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE) from None
        return reply_from_response(request, response, usage)
