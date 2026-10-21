"""A scripted provider for offline contract tests, never a production fallback."""

from datetime import datetime, timezone

from autohedge.ai import ModelMessage, ModelReply, ModelRequest, ModelTarget, TokenUsage

NOW = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)


def model_request(**changes):
    values = dict(
        target=ModelTarget(provider="fixture", model="local-test-v1"),
        messages=(ModelMessage(role="user", content="Analyze frozen evidence."),),
    )
    return ModelRequest(**{**values, **changes})


def model_reply(request, **changes):
    values = dict(
        request_id=request.request_id,
        target=request.target,
        resolved_model="local-test-v1-revision2",
        finish_reason="complete",
        content="There is insufficient evidence for a new opportunity.",
        usage=TokenUsage(status="known", input_tokens=42, output_tokens=9),
    )
    return ModelReply(**{**values, **changes})


class FakeModelProvider:
    def __init__(self, reply_factory=model_reply, *, error=None):
        self.reply_factory = reply_factory
        self.error = error
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reply_factory(request)
