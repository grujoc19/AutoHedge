"""Application-owned text bridge and bounded role runner for existing Swarms agents."""

import json
import time

from autohedge.ai.audit import ModelCallAudit
from autohedge.ai.client import ModelProvider, complete_model, utc_now
from autohedge.ai.config import AgentRole, RoleModelConfig
from autohedge.ai.contracts import ModelMessage, ModelRequest
from autohedge.ai.errors import ModelCallError, ModelErrorCode


class SwarmsModelBridge:
    def __init__(
        self,
        provider: ModelProvider,
        config: RoleModelConfig,
        *,
        clock=utc_now,
        monotonic=time.monotonic,
    ):
        self.provider = provider
        self.config = RoleModelConfig.model_validate(config)
        self.clock, self.monotonic = clock, monotonic
        self.begin()

    def begin(self):
        self.attempted = False
        self.result = None
        self.failure = None

    def run(self, task=None, *, messages=None, **kwargs):
        try:
            if self.attempted or kwargs:
                raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
            self.attempted = True
            if messages is None:
                messages = (ModelMessage(role="user", content=task),)
            elif task is not None:
                raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
            request = ModelRequest(messages=messages, **self.config.model_dump())
            self.result = complete_model(
                self.provider, request, clock=self.clock, monotonic=self.monotonic
            )
            return self.result.reply.content
        except Exception as error:
            self.failure = (
                error
                if isinstance(error, ModelCallError)
                else ModelCallError(ModelErrorCode.INVALID_REQUEST)
            )
            raise self.failure from None


class SwarmsResearchAgent:
    def __init__(
        self, agent, bridge: SwarmsModelBridge, role: AgentRole, *, search=None
    ):
        self.agent, self.bridge, self.role, self.search = agent, bridge, role, search

    @property
    def last_model_calls(self):
        return (
            (ModelCallAudit.from_result(self.role, self.bridge.result),)
            if self.bridge.result is not None and self.bridge.failure is None
            else ()
        )

    def run(self, task: str) -> str:
        self.bridge.begin()
        if self.search is not None:
            try:
                payload = json.loads(task)
            except (ValueError, TypeError):
                payload = {"request": {"task": task}}
            if not isinstance(payload, dict) or not isinstance(
                payload.get("request"), dict
            ):
                raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
            if payload["request"].get("snapshot") is None:
                query = payload["request"].get("task")
                if not isinstance(query, str) or not query.strip():
                    raise ModelCallError(ModelErrorCode.INVALID_REQUEST)
                query = query.strip()
                news = json.loads(self.search(query[:5000]))
                news = {
                    **news,
                    "query": query[:5000],
                    "query_truncated": len(query) > 5000,
                }
                task = json.dumps({**payload, "supplemental_news": news})
        try:
            output = self.agent.run(task=task)
        except Exception:
            if self.bridge.failure is not None:
                raise self.bridge.failure from None
            raise ModelCallError(ModelErrorCode.PROVIDER_FAILURE) from None
        if self.bridge.failure is not None:
            raise self.bridge.failure from None
        if self.bridge.result is None or output != self.bridge.result.reply.content:
            raise ModelCallError(ModelErrorCode.INVALID_RESPONSE)
        return output
