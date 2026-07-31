from __future__ import annotations

import re
from collections.abc import Iterator

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class MireaAdapter(BaseAdapter):
    code = "mirea"
    name = "МИРЭА"
    BASE = "https://priem.mirea.ru"
    CATALOG_URL = f"{BASE}/competitions_api"
    ENTRANTS_URL = f"{BASE}/competitions_api/entrants"
    ORG_UNIT_MOSCOW = "1484028700495285107"
    LEVELS = (2, 5)
    FORMS = (1, 3)

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient) -> Iterator[FetchResult]:
        probe = client.get(f"{self.BASE}/")
        if probe.status_code == 403 or "ddos-guard" in probe.text.lower():
            raise RuntimeError(
                "МИРЭА недоступен (DDoS-Guard / geo-block). "
                "В Hiddify: Region ≠ Russia, узел Москва, Proxy service only, затем "
                "` .venv/bin/python -m ingest -u mirea --proxy http://127.0.0.1:12334`"
            )

        yielded = 0
        for level in self.LEVELS:
            for form in self.FORMS:
                catalog = self._fetch_catalog(client, level=level, form=form)
                for program in catalog:
                    program_title = (
                        program.get("programSubjectTitle")
                        or program.get("title")
                        or "программа"
                    )
                    for competition in program.get("competitions") or []:
                        for comp_id in competition.get("compIds") or []:
                            if self.max_lists is not None and yielded >= self.max_lists:
                                return
                            try:
                                groups = self._fetch_entrants(
                                    client, competition_id=str(comp_id)
                                )
                            except Exception as error:  # noqa: BLE001
                                print(
                                    f"  skip  {program_title} / {comp_id}: {error}",
                                    flush=True,
                                )
                                continue
                            for group in groups:
                                rows = self._rows_from_group(group)
                                title = group.get("title") or competition.get("compType") or ""
                                program_set = (
                                    group.get("programSetTitle")
                                    or program.get("title")
                                    or program_title
                                )
                                is_budget = self._is_budget(
                                    f"{title} {competition.get('compType') or ''}"
                                )
                                seats_raw = group.get("plan")
                                if seats_raw is None:
                                    seats_raw = competition.get("plan")
                                seats = (
                                    int(seats_raw)
                                    if str(seats_raw).isdigit() and int(seats_raw) > 0
                                    else None
                                )
                                yielded += 1
                                yield FetchResult(
                                    university=self.name,
                                    program=(
                                        f"{program_title} — {program_set} — {title}"
                                    ).strip(" —"),
                                    is_budget=is_budget,
                                    rows=rows,
                                    source_url=self.ENTRANTS_URL,
                                    seats=seats,
                                )

    def _fetch_catalog(self, client: HttpClient, *, level: int, form: int) -> list[dict]:
        response = client.get(
            self.CATALOG_URL,
            params={
                "edu_level_id": level,
                "edu_form_id": form,
                "org_unit_id": self.ORG_UNIT_MOSCOW,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code == 403 or "ddos-guard" in response.text.lower():
            raise RuntimeError(
                "МИРЭА: DDoS-Guard на /competitions_api. Нужен RU egress без bypass .ru."
            )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "competitions", "items", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _fetch_entrants(self, client: HttpClient, *, competition_id: str) -> list[dict]:
        response = client.get(
            self.ENTRANTS_URL,
            params=[("competitions[]", competition_id)],
            headers={"Accept": "application/json"},
        )
        if response.status_code == 403 or "ddos-guard" in response.text.lower():
            raise RuntimeError("МИРЭА: DDoS-Guard на /competitions_api/entrants.")
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        return []

    @staticmethod
    def _is_budget(blob: str) -> bool:
        text = blob.lower()
        if any(
            token in text
            for token in ("платн", "договор", "коммерч", "внебюджет", "полное возмещение")
        ):
            return False
        return True

    def _rows_from_group(self, group: dict) -> list[CanonicalRow]:
        entrants = group.get("entrants") or []
        rows: list[CanonicalRow] = []
        for index, item in enumerate(entrants, start=1):
            # superCode is the ЕПГУ number other universities publish, so it is
            # what makes cross-university lookups work. SNILS is a fallback.
            code = re.sub(r"\D", "", str(item.get("superCode") or ""))
            if len(code) < 6:
                code = re.sub(r"\D", "", str(item.get("snils") or ""))
            if len(code) < 6:
                continue
            marks = item.get("marks") or []
            exam_scores = (
                " ".join(str(mark) for mark in marks if mark not in (None, ""))
                or dash(item.get("entranceMark") or item.get("em"))
            )
            confirmation = "—"
            if int(item.get("accepted") or 0) == 1:
                confirmation = "Электронное"
            elif int(item.get("origIn") or 0) == 1 or int(item.get("ioi") or 0) == 1:
                confirmation = "Электронное"
            rows.append(
                CanonicalRow(
                    position=dash(item.get("place") or index),
                    priority=dash(item.get("priority") or item.get("p")),
                    confirmation=confirmation,
                    total_score=dash(item.get("finalMark") or item.get("fm")),
                    exam_scores=exam_scores,
                    individual_score=dash(
                        item.get("achievementMark") or item.get("am")
                    ),
                    status=dash(item.get("s") or "Участвуете в конкурсе"),
                    applicant_code=code,
                )
            )
        return rows
