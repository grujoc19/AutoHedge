"""Shared HTTP transport with safe errors and opt-in bounded read retries."""

import json
import time
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
from email.utils import parsedate_to_datetime

import httpx

from autohedge.config import Settings
from autohedge.errors import ProviderError


def _retry_delay(response: httpx.Response) -> float | None:
    """Never shorten a server's Retry-After; abandon retries above two seconds."""
    header = response.headers.get("retry-after")
    if header is None:
        return 0.25
    try:
        delay = float(header)
    except ValueError:
        try:
            delay = (
                parsedate_to_datetime(header) - datetime.now(timezone.utc)
            ).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, delay) if 0 <= delay <= 2 else None


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def request_json(
    provider: str,
    method: str,
    url: str,
    *,
    settings: Settings,
    client: httpx.Client | None = None,
    read_only_retries: bool = False,
    precise: bool = False,
    sleep=time.sleep,
    **kwargs,
):
    """One attempt by default; explicit reads get at most two attempts.

    POST searches retry transient HTTP rejections, never ambiguous transport
    failures. Signing/order calls must leave read_only_retries disabled.
    """
    method = method.upper()
    if read_only_retries and method not in {"GET", "POST"}:
        raise ValueError("Read retries support GET or explicit POST searches only")

    def send(http):
        attempts = 2 if read_only_retries else 1
        for attempt in range(attempts):
            retry_available = attempt + 1 < attempts
            try:
                response = http.request(
                    method, url, timeout=settings.request_timeout, **kwargs
                )
                if (
                    retry_available
                    and response.status_code in {429, 502, 503, 504}
                    and (delay := _retry_delay(response)) is not None
                ):
                    sleep(delay)
                    continue
                response.raise_for_status()
                if precise:
                    return json.loads(
                        response.content,
                        parse_float=Decimal,
                        parse_constant=_reject_constant,
                        object_pairs_hook=_unique_object,
                    )
                return response.json()
            except httpx.HTTPStatusError as error:
                raise ProviderError(
                    provider, "request rejected", error.response.status_code
                ) from None
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if retry_available and method == "GET":
                    sleep(0.25)
                    continue
                reason = (
                    "request timed out"
                    if isinstance(error, httpx.TimeoutException)
                    else "connection failed"
                )
                raise ProviderError(provider, reason) from None
            except httpx.RequestError:
                raise ProviderError(provider, "connection failed") from None
            except (ValueError, DecimalException, RecursionError):
                raise ProviderError(provider, "invalid JSON response") from None

    if client is not None:
        return send(client)
    with httpx.Client() as http:
        return send(http)
