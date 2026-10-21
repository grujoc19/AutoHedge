"""Internal boundaries shared by normalized provider adapters."""

from datetime import datetime, timezone

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from autohedge.errors import ProviderError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_clock(clock, provider: str) -> datetime:
    try:
        return (
            TypeAdapter(AwareDatetime).validate_python(clock()).astimezone(timezone.utc)
        )
    except (ValidationError, ValueError, TypeError):
        raise ProviderError(provider, "invalid receipt clock") from None
