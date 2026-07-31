from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class MospolyAdapter(BaseAdapter):
    code = "mospoly"
    name = "МПУ"
    PAGE_URL = (
        "https://mospolytech.ru/postupayushchim/priem-v-universitet/"
        "rating-abiturientov/"
    )
    DATA_URL = (
        "https://mospolytech.ru/postupayushchim/priem-v-universitet/"
        "rating-abiturientov/fio_list_curl.php"
    )

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient):
        # MosPoly needs verify=False in some environments (handled by CLI).
        page = client.get(self.PAGE_URL)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "lxml")
        options = []
        for opt in soup.select("#select2 option"):
            spec = (opt.get("spec_code") or "").strip()
            value = (opt.get("value") or "").strip()
            title = opt.get_text(" ", strip=True)
            if not spec or not value or value == "0":
                continue
            # Focus on Moscow bachelor/specialist category codes.
            if not value.startswith("000000066_01"):
                continue
            options.append((value, spec, title))

        yielded = 0
        for select1, spec_code, title in options:
            for edu_fin, is_budget in (
                ("Бюджетная основа", True),
                ("Полное возмещение затрат", False),
            ):
                if self.max_lists is not None and yielded >= self.max_lists:
                    return
                rows = self._fetch_table(
                    client,
                    select1=select1,
                    spec_code=spec_code,
                    edu_fin=edu_fin,
                    is_budget=is_budget,
                )
                yielded += 1
                yield FetchResult(
                    university=self.name,
                    program=f"{title} ({edu_fin}, очная)",
                    is_budget=is_budget,
                    rows=rows,
                    source_url=self.DATA_URL,
                )

    def _fetch_table(
        self,
        client: HttpClient,
        *,
        select1: str,
        spec_code: str,
        edu_fin: str,
        is_budget: bool,
    ) -> list[CanonicalRow]:
        response = client.post(
            self.DATA_URL,
            data={
                "select1": select1,
                "specCode": spec_code,
                "eduForm": "Очная",
                "eduFin": edu_fin,
                "f": "1",
            },
        )
        response.raise_for_status()
        if response.text.strip() in {"!error!", ""}:
            return []
        soup = BeautifulSoup(response.text, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return []
        table = max(tables, key=lambda item: len(item.find_all("tr")))
        rows_raw = table.find_all("tr")
        # MosPoly emits a broken first header row (one huge concatenated cell +
        # real titles) and a clean second header. Prefer the clean one.
        header_idx = 0
        headers: list[str] = []
        candidates: list[tuple[int, list[str]]] = []
        for idx, tr in enumerate(rows_raw[:5]):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any(cell == "Уникальный код" for cell in cells):
                candidates.append((idx, cells))
        if candidates:
            header_idx, headers = min(
                candidates,
                key=lambda item: max((len(cell) for cell in item[1]), default=0),
            )
        if not headers:
            return []

        def col(*names: str) -> int | None:
            for name in names:
                exact = [
                    i
                    for i, header in enumerate(headers)
                    if header.strip() == name
                ]
                if exact:
                    return exact[0]
            for name in names:
                partial = [
                    i
                    for i, header in enumerate(headers)
                    if name.lower() in header.lower() and len(header) < 80
                ]
                if partial:
                    return partial[0]
            return None

        idx_pos = col("№")
        idx_real = col("Реальный рейтинг")
        idx_code = col("Уникальный код")
        idx_priority = col("Приоритет")
        idx_consent = col("Согласие", "Заключенный договор", "Договор")
        idx_total = col("Конкурсный балл", "Сумма баллов")
        idx_exam = col("Сумма баллов по предметам")
        idx_id = col("Сумма ИД")
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
            consent_raw = (
                cells[idx_consent] if idx_consent is not None and idx_consent < len(cells) else ""
            ).lower()
            if is_budget:
                confirmation = (
                    "Электронное"
                    if consent_raw in {"да", "есть", "✓"}
                    else "—"
                )
            else:
                confirmation = "Да" if consent_raw in {"да", "есть", "✓"} else "—"
            real = (
                cells[idx_real]
                if idx_real is not None and idx_real < len(cells)
                else ""
            ).strip()
            ordinal = (
                cells[idx_pos]
                if idx_pos is not None and idx_pos < len(cells)
                else ""
            ).strip()
            results.append(
                CanonicalRow(
                    position=dash(real or ordinal or len(results) + 1),
                    priority=dash(cells[idx_priority] if idx_priority is not None else "—"),
                    confirmation=confirmation,
                    total_score=dash(cells[idx_total] if idx_total is not None else "—"),
                    exam_scores=dash(cells[idx_exam] if idx_exam is not None else "—"),
                    individual_score=dash(cells[idx_id] if idx_id is not None else "—"),
                    status="Участвуете в конкурсе",
                    applicant_code=code,
                )
            )
        return results
