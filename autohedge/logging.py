"""One Loguru configuration, contextual run IDs and secret-safe output."""

import re
import sys
from threading import RLock

from loguru import logger

from autohedge.config import Settings

_lock = RLock()
_secrets: set[str] = set()
_credentials = re.compile(
    r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?|"
    r"(?:x-cg-pro-api-key|x-api-key|api[_-]?key|private[_-]?key|token)[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}]+)"
)


def redact(value: str) -> str:
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return _credentials.sub(r"\1[REDACTED]", value)


def _safe_value(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"(?i)key|token|authorization|secret", str(key))
            else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return redact(str(value))


def _patch(record):
    name = record["name"] or ""
    if name.split(".")[0] in {"swarms", "litellm", "openai", "httpx", "httpcore"}:
        # Swarms formats prompts, agent state and tracebacks into plain messages.
        # Redacting keys alone would still persist raw user/model data.
        record["message"] = (
            f"{name}: dependency event ({record['level'].name}); details suppressed"
        )
        record["extra"] = {
            key: value
            for key, value in record["extra"].items()
            if key in {"run_id", "stage"}
        }
    record["message"] = redact(record["message"])
    record["extra"] = _safe_value(record["extra"])
    record["extra"].setdefault("run_id", "-")
    record["extra"].setdefault("stage", "-")
    # Exception tracebacks can contain request URLs, keys and raw model responses.
    record["exception"] = None


def configure_logging(settings: Settings, *, sink=None):
    """Own process-wide Loguru sinks, replacing Swarms' implicit configuration.

    Call explicitly at the application boundary. Embedders may supply a sink.
    """
    with _lock:
        _secrets.update(settings.secret_values())
        logger.remove()
        logger.configure(patcher=_patch)
        options = dict(
            level=settings.log_level,
            backtrace=False,
            diagnose=False,
            format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {extra[run_id]} | {extra[stage]} | {message}",
        )
        logger.add(sink if sink is not None else sys.stderr, **options)
        if settings.log_file is not None:
            settings.log_file.parent.mkdir(parents=True, exist_ok=True)
            logger.add(settings.log_file, rotation="10 MB", retention=3, **options)
