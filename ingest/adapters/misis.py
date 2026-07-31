from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class MisisAdapter(BaseAdapter):
    code = "misis"
    name = "МИСИС"
    INDEX_URL = (
        "https://misis.ru/applicants/admission/progress/"
        "baccalaureate-and-specialties/list-of-applicants/"
    )

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient):
        page = client.get(self.INDEX_URL)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "lxml")
        links: list[tuple[str, str, bool]] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if "list/?id=" not in href:
                continue
            list_id = href.split("list/?id=", 1)[1].split("&", 1)[0]
            is_budget = "BUDJ" in list_id.upper() or "BUD" in list_id.upper()
            if "VNEBUD" in list_id.upper() or "PLAT" in list_id.upper():
                is_budget = False
            if "BUDJ" not in list_id.upper() and (
                "VNE" in list_id.upper() or "DOG" in list_id.upper()
            ):
                is_budget = False
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if not title:
                title = list_id
            links.append((urljoin(self.INDEX_URL, href), title, is_budget))

        unique: dict[str, tuple[str, bool]] = {}
        for url, title, is_budget in links:
            unique[url] = (title, is_budget)

        yielded = 0
        for url, (title, is_budget) in unique.items():
            if self.max_lists is not None and yielded >= self.max_lists:
                return
            list_id = url.split("id=", 1)[-1]
            if "VNEBUD" in list_id.upper() or "-VNE" in list_id.upper():
                is_budget = False
            elif "BUDJ" in list_id.upper():
                is_budget = True
            rows = self._parse_list(client, url)
            program = title or list_id
            marker = ""
            if "-OKM-" in list_id:
                marker = " общий конкурс"
            elif "-OK-" in list_id:
                marker = " особая квота"
            elif "-OTK-" in list_id:
                marker = " отдельная квота"
            elif "-CK-" in list_id or "CEL" in list_id.upper():
                marker = " целевая квота"
            yielded += 1
            yield FetchResult(
                university=self.name,
                program=f"{program}{marker}".strip(),
                is_budget=is_budget,
                rows=rows,
                source_url=url,
            )

    def _parse_list(self, client: HttpClient, url: str) -> list[CanonicalRow]:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        table = soup.find("table")
        if table is None:
            return []

        rows_raw = table.find_all("tr")
        if len(rows_raw) < 2:
            return []

        # header may span two rows; find the row with Уникальный код
        header_idx = 0
        headers: list[str] = []
        for idx, tr in enumerate(rows_raw[:5]):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any("Уникальный код" in cell for cell in cells):
                header_idx = idx
                headers = cells
                break
        if not headers:
            return []

        def col(*names: str) -> int | None:
            for name in names:
                for i, header in enumerate(headers):
                    if name.lower() in header.lower():
                        return i
            return None

        idx_pos = col("№ п/п", "№")
        idx_code = col("Уникальный код")
        idx_priority = col("Приоритет зачисления", "Приоритет")
        idx_consent = col("Согласие")
        idx_total = col("Общая сумма баллов", "Конкурсный")
        idx_exam = col("Сумма баллов по предметам", "Баллы ЕГЭ")
        idx_id = col("Баллы ИД")
        idx_status = col("Статус")
        if idx_code is None:
            return []

        results: list[CanonicalRow] = []
        for tr in rows_raw[header_idx + 1 :]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= idx_code:
                continue
            code = re.sub(r"\D", "", cells[idx_code])
            if not code:
                continue
            consent_raw = cells[idx_consent] if idx_consent is not None and idx_consent < len(cells) else "—"
            consent = (
                "Электронное"
                if consent_raw.lower() in {"да", "есть", "✓", "электронное", "бумажное"}
                else "—"
            )
            if consent_raw.lower() in {"бумажное"}:
                consent = "Бумажное"
            results.append(
                CanonicalRow(
                    position=dash(cells[idx_pos] if idx_pos is not None else len(results) + 1),
                    priority=dash(cells[idx_priority] if idx_priority is not None else "—"),
                    confirmation=consent if consent != "—" else dash(consent_raw),
                    total_score=dash(cells[idx_total] if idx_total is not None else "—"),
                    exam_scores=dash(cells[idx_exam] if idx_exam is not None else "—"),
                    individual_score=dash(cells[idx_id] if idx_id is not None else "—"),
                    status=dash(cells[idx_status] if idx_status is not None else "Участвуете в конкурсе"),
                    applicant_code=code,
                )
            )
        return results
