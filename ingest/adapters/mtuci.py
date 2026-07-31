from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class MtuciAdapter(BaseAdapter):
    code = "mtuci"
    name = "МТУСИ"
    LEVELS = {
        "bak_main": "Бакалавриат/специалитет",
        "mag_main": "Магистратура",
    }
    FINANCE = {
        "budg": True,
        "pvz": False,
    }

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient):
        yielded = 0
        for level, level_label in self.LEVELS.items():
            for finance, is_budget in self.FINANCE.items():
                url = (
                    "https://abitur.mtuci.ru/ranked_lists/spisok.php?"
                    + urlencode(
                        {
                            "valueSearch": "",
                            "levelTarget": level,
                            "priznakViev": finance,
                            "originalView": "all",
                        }
                    )
                )
                page = client.get(url)
                page.raise_for_status()
                soup = BeautifulSoup(page.text, "lxml")
                select = soup.find("select", attrs={"name": "group"})
                if select is None:
                    continue
                options = [
                    (opt.get("value") or "").strip()
                    for opt in select.find_all("option")
                    if (opt.get("value") or "").strip()
                ]
                for group in options:
                    if self.max_lists is not None and yielded >= self.max_lists:
                        return
                    list_url = (
                        "https://abitur.mtuci.ru/ranked_lists/spisok.php?"
                        + urlencode(
                            {
                                "group": group,
                                "levelTarget": level,
                                "priznakViev": finance,
                                "originalView": "all",
                            }
                        )
                    )
                    rows = self._parse_group(client, list_url, is_budget=is_budget)
                    yielded += 1
                    yield FetchResult(
                        university=self.name,
                        program=f"{group} [{level_label}]",
                        is_budget=is_budget,
                        rows=rows,
                        source_url=list_url,
                    )

    def _parse_group(
        self, client: HttpClient, url: str, *, is_budget: bool
    ) -> list[CanonicalRow]:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        table = soup.find("table")
        if table is None:
            return []
        rows_raw = table.find_all("tr")
        if len(rows_raw) < 2:
            return []
        headers = [c.get_text(" ", strip=True) for c in rows_raw[0].find_all(["th", "td"])]

        def col(*names: str) -> int | None:
            for name in names:
                for i, header in enumerate(headers):
                    if name.lower() in header.lower():
                        return i
            return None

        idx_pos = col("Номер ПП", "№")
        idx_code = col("Уникальный код")
        idx_priority = col("Приоритет")
        idx_consent = col("Согласие", "Договор")
        idx_total = col("Сумма баллов")
        idx_id = col("Сумма баллов за ИД", "ИД")
        if idx_code is None:
            return []

        # exams: gather subject columns between total and id if present
        exam_idxs = [
            i
            for i, header in enumerate(headers)
            if i not in {idx_pos, idx_code, idx_priority, idx_consent, idx_total, idx_id}
            and header
            and "личное дело" not in header.lower()
            and "зачислен" not in header.lower()
            and "оригинал" not in header.lower()
        ]

        results: list[CanonicalRow] = []
        for tr in rows_raw[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= idx_code:
                continue
            code = re.sub(r"\D", "", cells[idx_code])
            if not code:
                continue
            consent_raw = (
                cells[idx_consent] if idx_consent is not None and idx_consent < len(cells) else "—"
            )
            if consent_raw.lower() in {"да", "✓", "есть"}:
                confirmation = "Электронное" if is_budget else "Да"
            elif consent_raw.lower() in {"бумажное"} and is_budget:
                confirmation = "Бумажное"
            else:
                confirmation = "—"
            exams = [
                cells[i]
                for i in exam_idxs
                if i < len(cells) and cells[i] and cells[i] != "—"
            ][:5]
            results.append(
                CanonicalRow(
                    position=dash(cells[idx_pos] if idx_pos is not None else len(results) + 1),
                    priority=dash(cells[idx_priority] if idx_priority is not None else "—"),
                    confirmation=confirmation,
                    total_score=dash(cells[idx_total] if idx_total is not None else "—"),
                    exam_scores=" ".join(exams) if exams else "—",
                    individual_score=dash(cells[idx_id] if idx_id is not None else "—"),
                    status="Участвуете в конкурсе",
                    applicant_code=code,
                )
            )
        return results
