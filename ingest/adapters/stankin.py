from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class StankinAdapter(BaseAdapter):
    code = "stankin"
    name = "СТАНКИН"
    PAGE_URL = "https://priem.stankin.ru/bakalavriatispetsialitet/ranked-lists/"
    GRID_URL = "https://priem.stankin.ru/gridspisokpostupayushchikh"

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient):
        page = client.get(self.PAGE_URL)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "lxml")
        program_select = soup.find("select", attrs={"name": "PROPERTY_394"})
        programs = [
            (opt.get("value") or "").strip()
            for opt in (program_select.find_all("option") if program_select else [])
            if (opt.get("value") or "").strip()
        ]
        yielded = 0
        for budget_label, is_budget in (
            ("Бюджетная основа", True),
            ("Полное возмещение затрат", False),
        ):
            for program in programs:
                if self.max_lists is not None and yielded >= self.max_lists:
                    return
                params = {
                    "apply_filter": "Y",
                    "PROPERTY_584": "ready",
                    "PROPERTY_388": budget_label,
                    "PROPERTY_389": "1 - Очная",
                    "PROPERTY_394": program,
                    "PROPERTY_400": "✓",
                    "PROPERTY_710": "✓",
                    "PROPERTY_423": "",
                    "PROPERTY_402": "",
                    "PROPERTY_396": "",
                    "COL_CITIZENSHIP": "",
                    "PROPERTY_747": "-",
                    "LIST_TYPE": "ranked",
                    "EDU_LEVEL": "bs",
                }
                url = f"{self.GRID_URL}?{urlencode(params)}"
                rows = self._parse_grid(client, url)
                yielded += 1
                yield FetchResult(
                    university=self.name,
                    program=f"{program} ({budget_label})",
                    is_budget=is_budget,
                    rows=rows,
                    source_url=url,
                )

    def _parse_grid(self, client: HttpClient, url: str) -> list[CanonicalRow]:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        table = None
        for candidate in soup.find_all("table"):
            header = " ".join(
                c.get_text(" ", strip=True)
                for c in candidate.find_all("tr")[:1]
                for c in candidate.find_all(["th", "td"])[:8]
            )
            if "Уникальный код" in header:
                table = candidate
                break
        if table is None:
            return []
        rows_raw = table.find_all("tr")
        headers = [c.get_text(" ", strip=True) for c in rows_raw[0].find_all(["th", "td"])]

        def col(*names: str) -> int | None:
            for name in names:
                for i, header in enumerate(headers):
                    if name.lower() in header.lower():
                        return i
            return None

        idx_pos = col("№")
        idx_code = col("Уникальный код")
        idx_priority = col("Приоритет")
        idx_consent = col("Согласие")
        idx_total = col("Сумма баллов с ИД", "Сумма баллов")
        idx_id = col("ИД")
        idx_status = col("Статус")
        if idx_code is None:
            return []

        results: list[CanonicalRow] = []
        for tr in rows_raw[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= idx_code:
                continue
            code = re.sub(r"\D", "", cells[idx_code])
            if not code:
                continue
            consent_raw = (
                cells[idx_consent] if idx_consent is not None and idx_consent < len(cells) else ""
            )
            confirmation = (
                "Электронное"
                if consent_raw in {"✓", "да", "Да"}
                else "—"
            )
            results.append(
                CanonicalRow(
                    position=dash(cells[idx_pos] if idx_pos is not None else len(results) + 1),
                    priority=dash(cells[idx_priority] if idx_priority is not None else "—"),
                    confirmation=confirmation,
                    total_score=dash(cells[idx_total] if idx_total is not None else "—"),
                    exam_scores="—",
                    individual_score=dash(cells[idx_id] if idx_id is not None else "—"),
                    status=dash(
                        cells[idx_status] if idx_status is not None else "Участвуете в конкурсе"
                    ),
                    applicant_code=code,
                )
            )
        return results
