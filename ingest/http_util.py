from __future__ import annotations

import time
from typing import Any

import httpx


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 120.0,
        verify: bool = True,
        delay_seconds: float = 0.35,
        proxy: str | None = None,
        retries: int = 4,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.retries = retries
        kwargs: dict[str, Any] = {
            "headers": DEFAULT_HEADERS,
            "timeout": timeout,
            "follow_redirects": True,
            "verify": verify,
        }
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            time.sleep(self.delay_seconds)
            try:
                return self._client.request(method, url, **kwargs)
            except (
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.ProxyError,
            ) as error:
                last_error = error
                time.sleep(min(2**attempt, 8))
        assert last_error is not None
        raise last_error

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("POST", url, **kwargs)
