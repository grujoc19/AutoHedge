"""Safe application errors: never include raw provider responses or credentials."""


class AutoHedgeError(Exception):
    """Base class for errors that may be displayed by the CLI."""


class ConfigurationError(AutoHedgeError):
    pass


class ProviderError(AutoHedgeError):
    def __init__(self, provider: str, reason: str, status_code: int | None = None):
        self.provider = provider
        self.reason = reason
        self.status_code = status_code
        status = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{provider}: {reason}{status}")


class ResearchError(AutoHedgeError):
    def __init__(self, stage: str, reason: str):
        self.stage = stage
        super().__init__(f"Research stopped at {stage}: {reason}")


class DataQualityError(AutoHedgeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Market data rejected: {code}")
