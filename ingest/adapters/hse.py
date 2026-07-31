from __future__ import annotations

from collections.abc import Iterator

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class HseAdapter(BaseAdapter):
    code = "hse"
    name = "ВШЭ"
    GROUPS_URL = "https://pk.hse.ru/admissions/api/competitve-group"
    APPLICANT_URL = "https://pk.hse.ru/admissions/api/applicant"
    PAGE_SIZE = 100

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient) -> Iterator[FetchResult]:
        response = client.get(self.GROUPS_URL, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
        yielded = 0

        for filial in payload.get("filials", []):
            filial_name = filial.get("name") or "филиал"
            if filial_name != "Москва":
                continue
            for direction in filial.get("trainingDirections", []):
                direction_name = direction.get("name") or ""
                for program in direction.get("educationPrograms", []):
                    program_name = program.get("name") or direction_name or "программа"
                    for group in program.get("competitiveGroups", []):
                        if self.max_lists is not None and yielded >= self.max_lists:
                            return
                        place = group.get("placeType") or {}
                        set_group = group.get("setOfCompetitiveGroup") or {}
                        place_id = place.get("id")
                        set_id = set_group.get("id")
                        group_id = group.get("id")
                        if not place_id or not set_id or not group_id:
                            continue
                        # Only "К" (с оплатой обучения) is paid; "Б", "ЦД", "Ц",
                        # "Л", "В" are budget places within different quotas.
                        place_code = (place.get("code") or "").upper()
                        place_name = (place.get("name") or "").lower()
                        is_budget = not (
                            place_code in {"К", "K", "Д", "П"}
                            or "оплат" in place_name
                            or "плат" in place_name
                            or "договор" in place_name
                        )
                        rows = self._fetch_applicants(
                            client,
                            competitive_group_id=group_id,
                            set_of_group_id=set_id,
                            place_type_id=place_id,
                        )
                        label = group.get("name") or program_name
                        place_label = place.get("name") or ""
                        yielded += 1
                        yield FetchResult(
                            university=self.name,
                            program=" ".join(
                                part
                                for part in (label, f"[{place_label}, {filial_name}]")
                                if part
                            ),
                            is_budget=is_budget,
                            rows=rows,
                            source_url=self.APPLICANT_URL,
                        )

    def _fetch_applicants(
        self,
        client: HttpClient,
        *,
        competitive_group_id: str,
        set_of_group_id: str,
        place_type_id: str,
    ) -> list[CanonicalRow]:
        rows: list[CanonicalRow] = []
        page = 0
        while True:
            response = client.get(
                self.APPLICANT_URL,
                params={
                    "sort": "index_number_in_comp_list",
                    "level": "BAK",
                    "competitiveGroupId": competitive_group_id,
                    "placeType": place_type_id,
                    "setOfCompetitiveGroupId": set_of_group_id,
                    "page": page,
                    "size": self.PAGE_SIZE,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            content = payload.get("content") or []
            for item in content:
                code = str(item.get("idEpgu") or "").strip()
                if not code.isdigit():
                    continue
                exams = item.get("applicantEntranceTests") or []
                exam_scores = " ".join(
                    str(int(exam["score"]))
                    if isinstance(exam.get("score"), float)
                    and float(exam["score"]).is_integer()
                    else str(exam.get("score"))
                    for exam in exams
                    if exam.get("score") is not None
                ) or "—"
                consent = "Электронное" if item.get("isConcertToEnrollment") else "—"
                contract = "Да" if item.get("isContract") or item.get("hasContract") else "—"
                position = item.get("indexNumberInCompList") or item.get(
                    "index_number_in_comp_list"
                )
                priority = item.get("priority") or item.get("priorityEnrollment")
                rows.append(
                    CanonicalRow(
                        position=dash(position if position is not None else len(rows) + 1),
                        priority=dash(priority),
                        confirmation=consent if consent != "—" else contract,
                        total_score=dash(
                            item.get("sumCompetitiveScore")
                            or item.get("total_competitive_score")
                        ),
                        exam_scores=exam_scores,
                        individual_score=dash(
                            item.get("achievementsSum")
                            or item.get("individual_achievements_score")
                        ),
                        status="Участвуете в конкурсе",
                        applicant_code=code,
                    )
                )
            total_pages = int(payload.get("totalPages") or 0)
            page += 1
            if page >= total_pages or not content:
                break
        return rows
