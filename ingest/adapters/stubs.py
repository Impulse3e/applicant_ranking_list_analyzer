from __future__ import annotations

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient


class MireaAdapter(BaseAdapter):
    code = "mirea"
    name = "МИРЭА"

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        response = client.get("https://priem.mirea.ru/accepted-entrants-list/")
        if response.status_code == 403 or "ddos-guard" in response.text.lower():
            raise RuntimeError(
                "МИРЭА недоступен из текущей сети (DDoS-Guard / geo-block). "
                "Нужен доступ с IP, который сайт не блокирует, либо официальный feed."
            )
        raise RuntimeError(
            f"МИРЭА: неожиданный ответ HTTP {response.status_code}. "
            "Адаптер требует доработки под актуальный фронтенд."
        )


class MiptAdapter(BaseAdapter):
    code = "mipt"
    name = "МФТИ"

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        raise RuntimeError(
            "МФТИ: списки отдаются Vue/Bitrix AJAX (`?method=getList`). "
            "Каркас страницы доступен, но стабильный массовый выгрузчик ещё не "
            "подключён — нужен отдельный разбор filter/component_id."
        )
