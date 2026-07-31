from __future__ import annotations

import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ingest.base import BaseAdapter, FetchResult
from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, dash


class MiptAdapter(BaseAdapter):
    code = "mipt"
    name = "МФТИ"
    PAGE_URL = "https://pk.mipt.ru/bachelor/competition-list/"
    AJAX_URL = (
        "https://pk.mipt.ru/bachelor/competition-list/"
        "?ajax=Y&action=_getNameListHtml"
    )

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        return list(self.iter_results(client))

    def iter_results(self, client: HttpClient) -> Iterator[FetchResult]:
        page = client.get(self.PAGE_URL)
        page.raise_for_status()
        catalog, sessid = self._parse_bootstrap(page.text)
        component_id = catalog.get("componentId")
        if not component_id or not sessid:
            raise RuntimeError("МФТИ: не найден componentId/sessid на странице списков.")

        conditions = catalog.get("conditionFullList") or {}
        if isinstance(conditions, list):
            conditions = {str(item.get("ID")): item for item in conditions if item.get("ID")}
        forms = {"2": True, "1": False}  # budget / contract
        competitves = catalog.get("competitiveFullList") or {}
        if isinstance(competitves, list):
            competitves = {str(item.get("ID")): item for item in competitves}

        # Prefer the newest season that still has groups.
        seasons = sorted(
            {
                str(item.get("SEASON_ID"))
                for item in competitves.values()
                if item.get("SEASON_ID") not in (None, "", "0")
            },
            key=lambda value: int(value) if value.isdigit() else -1,
            reverse=True,
        )
        if not seasons:
            seasons = ["13", "12", "11"]

        yielded = 0
        for season in seasons:
            season_groups = [
                item
                for item in competitves.values()
                if str(item.get("SEASON_ID")) == season and item.get("NAME")
            ]
            for group in season_groups:
                speciality_id = str(group.get("SPECIALITY_ID") or "")
                competitive_id = str(group.get("ID") or "")
                if not speciality_id or not competitive_id:
                    continue
                for form_id, is_budget in forms.items():
                    # Skip impossible financing when quota fields are empty.
                    if is_budget and not (
                        group.get("QUOTA")
                        or group.get("TARGET_QUOTA")
                        or group.get("SPECIAL_QUOTA")
                        or group.get("SUPER_SPACIAL_QUOTA")
                    ):
                        continue
                    if not is_budget and not group.get("CONTRACT"):
                        continue
                    condition_ids = ["1"]
                    if group.get("SPECIAL_QUOTA"):
                        condition_ids.append("4")
                    if group.get("TARGET_QUOTA") or group.get("FULL_TARGET"):
                        condition_ids.append("3")
                    condition_ids.append("2")
                    for condition_id in condition_ids:
                        if self.max_lists is not None and yielded >= self.max_lists:
                            return
                        condition = conditions.get(str(condition_id)) or {}
                        rows = self._fetch_table(
                            client,
                            sessid=sessid,
                            component_id=component_id,
                            season=season,
                            level="1",
                            condition=str(condition_id),
                            speciality=speciality_id,
                            competitive=competitive_id,
                            form=form_id,
                        )
                        condition_name = (
                            condition.get("NAME_SHORT")
                            or condition.get("NAME")
                            or str(condition_id)
                        )
                        form_label = "бюджет" if is_budget else "контракт"
                        program = (
                            f"{group.get('NAME')} [{condition_name}, {form_label}, "
                            f"сезон {season}]"
                        )
                        seats = None
                        if is_budget:
                            raw = group.get("QUOTA")
                            seats = int(raw) if str(raw).isdigit() else None
                        else:
                            raw = group.get("CONTRACT")
                            seats = int(raw) if str(raw).isdigit() else None
                        yielded += 1
                        yield FetchResult(
                            university=self.name,
                            program=program,
                            is_budget=is_budget,
                            rows=rows,
                            source_url=self.AJAX_URL,
                            seats=seats,
                        )

    def _parse_bootstrap(self, html: str) -> tuple[dict, str]:
        match = re.search(r"namesListExternalData\s*=\s*", html)
        if not match:
            raise RuntimeError("МФТИ: namesListExternalData не найден.")
        start = html.find("{", match.end() - 1)
        depth = 0
        end = None
        for index, char in enumerate(html[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            raise RuntimeError("МФТИ: не удалось разобрать namesListExternalData.")
        catalog = json.loads(html[start:end])
        sess = re.search(r"sessid['\"]?\s*[:=]\s*['\"]([a-f0-9]{32})['\"]", html)
        if not sess:
            raise RuntimeError("МФТИ: bitrix sessid не найден.")
        return catalog, sess.group(1)

    def _fetch_table(
        self,
        client: HttpClient,
        *,
        sessid: str,
        component_id: str,
        season: str,
        level: str,
        condition: str,
        speciality: str,
        competitive: str,
        form: str,
    ) -> list[CanonicalRow]:
        response = client.post(
            self.AJAX_URL,
            data={
                "sessid": sessid,
                "component_id": component_id,
                "filter[season]": season,
                "filter[level]": level,
                "filter[condition]": condition,
                "filter[speciality]": speciality,
                "filter[competitive]": competitive,
                "filter[form]": form,
                "filter[agreement]": "false",
                "filter[all_priorities]": "true",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise RuntimeError("МФТИ: ответ AJAX не JSON.") from error
        if payload.get("status") != "ok":
            return []
        html = payload.get("html") or ""
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.entrant-list") or soup.find("table")
        if table is None:
            return []

        headers: list[str] = []
        for tr in table.select("thead tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any("СНИЛС" in cell or "ИНД" in cell for cell in cells):
                headers = cells
                break
        if not headers:
            return []

        def col(*names: str) -> int | None:
            for name in names:
                for index, header in enumerate(headers):
                    if name.lower() in header.lower():
                        return index
            return None

        idx_pos = col("№")
        idx_priority = col("П*")
        idx_code = col("СНИЛС", "ИНД")
        idx_id = col("ИД")
        idx_sum_no = col("С-ма без ИД", "без ИД")
        idx_sum = col("С-ма с ИД", "с ИД")
        idx_consent = col("СЗ", "Согласие")
        if idx_code is None:
            return []

        results: list[CanonicalRow] = []
        body_rows = table.select("tbody.entrant-list-body tr") or table.find_all("tr")
        for tr in body_rows:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= idx_code:
                continue
            raw_code = cells[idx_code]
            code = re.sub(r"\D", "", raw_code)
            if len(code) < 6:
                continue
            consent_raw = (
                cells[idx_consent] if idx_consent is not None and idx_consent < len(cells) else ""
            ).lower()
            confirmation = (
                "Электронное"
                if consent_raw in {"да", "+", "✓", "есть", "электронное"}
                else "—"
            )
            exam = (
                cells[idx_sum_no]
                if idx_sum_no is not None and idx_sum_no < len(cells)
                else "—"
            )
            total = (
                cells[idx_sum] if idx_sum is not None and idx_sum < len(cells) else exam
            )
            results.append(
                CanonicalRow(
                    position=dash(
                        cells[idx_pos]
                        if idx_pos is not None and idx_pos < len(cells)
                        else len(results) + 1
                    ),
                    priority=dash(
                        cells[idx_priority]
                        if idx_priority is not None and idx_priority < len(cells)
                        else "—"
                    ),
                    confirmation=confirmation,
                    total_score=dash(total),
                    exam_scores=dash(exam),
                    individual_score=dash(
                        cells[idx_id] if idx_id is not None and idx_id < len(cells) else "—"
                    ),
                    status="Участвуете в конкурсе",
                    applicant_code=code,
                )
            )
        return results
