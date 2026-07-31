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
        timeout: float = 90.0,
        verify: bool = True,
        delay_seconds: float = 0.35,
    ) -> None:
        self.delay_seconds = delay_seconds
        self._client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            verify=verify,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        time.sleep(self.delay_seconds)
        return self._client.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        time.sleep(self.delay_seconds)
        return self._client.post(url, **kwargs)
