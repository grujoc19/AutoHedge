"""Shared HTTP transport and safe errors for existing provider adapters."""

import httpx

from autohedge.config import Settings
from autohedge.errors import ProviderError


def request_json(
    provider: str,
    method: str,
    url: str,
    *,
    settings: Settings,
    client: httpx.Client | None = None,
    **kwargs,
):
    def send(http):
        try:
            response = http.request(
                method, url, timeout=settings.request_timeout, **kwargs
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise ProviderError(
                provider, "request rejected", error.response.status_code
            ) from None
        except httpx.TimeoutException:
            raise ProviderError(provider, "request timed out") from None
        except httpx.RequestError:
            raise ProviderError(provider, "connection failed") from None
        except ValueError:
            raise ProviderError(provider, "invalid JSON response") from None

    if client is not None:
        return send(client)
    with httpx.Client() as http:
        return send(http)
