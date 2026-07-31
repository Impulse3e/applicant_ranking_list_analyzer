from __future__ import annotations

import csv
import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


APPLICANT_CODE_COLUMN = "Код поступающего"
FILENAME_PATTERN = re.compile(
    r"^(?P<program>.+)\.(?P<timestamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.csv$"
)
CODE_PATTERN = re.compile(r"^\d{1,20}$")

INACTIVE_STATUSES = {
    "Конкурсная группа исключена",
    "Вуз отклонил выбор конкурсной группы",
}

REASON_LABELS = {
    "budget_over_paid": "уйдут на бюджет",
    "higher_priority": "уйдут на конкурс с более высоким приоритетом",
    "confirmed_elsewhere": "уже подтвердили другой конкурс",
}


class DataSourceError(RuntimeError):
    """Raised when a ranking-list source cannot be safely loaded."""


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    file_name: str
    program: str
    list_type: str
    snapshot_at: str


@dataclass(frozen=True, slots=True)
class RankingEntry:
    source: SourceMetadata
    position: int | None
    priority: int | None
    total_score: str
    exam_scores: str
    individual_score: str
    status: str
    confirmation_label: str
    confirmation_value: str
    selected_at: str

    def to_public_dict(self) -> dict[str, object]:
        """Return only fields that may be sent to the browser."""
        payload = asdict(self)
        payload["source"] = asdict(self.source)
        return payload


@dataclass(frozen=True, slots=True)
class ListMember:
    digest: bytes
    entry: RankingEntry


def normalize_code(value: object) -> str:
    code = str(value).strip()
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError("Код поступающего должен содержать только цифры.")
    return code


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized == "—":
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _display_program(raw_name: str) -> str:
    parts = [part for part in raw_name.split("__") if part]
    if len(parts) >= 2:
        # university__program[__list_type]
        university = parts[0].replace("_", " ").strip()
        program = parts[1].replace("_", " ").strip()
        list_hint = parts[2].replace("_", " ").strip() if len(parts) >= 3 else ""
        if list_hint in {"бюджет", "платное"}:
            return f"{university} — {program}"
        if list_hint:
            return f"{university} — {program} — {list_hint}"
        return f"{university} — {program}"
    return raw_name.replace("__", " — ").replace("_", " ").strip()


def parse_source_metadata(path: Path, columns: Iterable[str]) -> SourceMetadata:
    match = FILENAME_PATTERN.fullmatch(path.name)
    if match is None:
        raise DataSourceError(
            f"Имя файла {path.name!r} не содержит ожидаемую дату снимка."
        )

    column_set = set(columns)
    if "Подано согласие" in column_set:
        list_type = "Бюджет"
    elif "Наличие договора" in column_set:
        list_type = "Платное обучение"
    else:
        list_type = "Тип не определен"

    snapshot = datetime.strptime(
        match.group("timestamp"), "%Y-%m-%d_%H-%M-%S"
    )
    return SourceMetadata(
        file_name=path.name,
        program=_display_program(match.group("program")),
        list_type=list_type,
        snapshot_at=snapshot.isoformat(timespec="seconds"),
    )


def _is_active(entry: RankingEntry) -> bool:
    return entry.status not in INACTIVE_STATUSES


def _is_confirmed(entry: RankingEntry) -> bool:
    value = entry.confirmation_value.strip()
    if entry.confirmation_label == "Подано согласие":
        return value in {"Бумажное", "Электронное"}
    if entry.confirmation_label == "Наличие договора":
        return value == "Да"
    return False


def _destination_label(entry: RankingEntry) -> str:
    priority = (
        f", приоритет {entry.priority}" if entry.priority is not None else ""
    )
    return f"{entry.source.program} ({entry.source.list_type}{priority})"


class RankingIndex:
    """In-memory HMAC index over local CSV ranking lists.

    Raw applicant codes are read one row at a time, converted to keyed digests,
    and are not retained in entries returned by the search API.
    """

    def __init__(self, secret: bytes, campaign: str = "2026") -> None:
        if len(secret) < 32:
            raise ValueError("HMAC secret must contain at least 32 bytes.")
        self._secret = secret
        self._campaign = campaign
        self._entries: dict[bytes, list[RankingEntry]] = {}
        self._lists: dict[str, list[ListMember]] = {}
        self._seats: dict[str, int] = {}
        self.source_count = 0
        self.entry_count = 0

    @classmethod
    def from_directory(
        cls,
        data_directory: Path,
        secret: bytes,
        campaign: str = "2026",
        seats_path: Path | None = None,
    ) -> RankingIndex:
        index = cls(secret=secret, campaign=campaign)
        paths = sorted(data_directory.glob("*.csv"))
        if not paths:
            # Empty catalog is allowed so the app can start before the first ingest.
            index._load_seats(seats_path or (data_directory / "seats.json"))
            return index

        for path in paths:
            index._load_file(path)

        resolved_seats = seats_path or (data_directory / "seats.json")
        index._load_seats(resolved_seats)
        return index

    def _digest(self, applicant_code: str) -> bytes:
        message = f"{self._campaign}:{applicant_code}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).digest()

    def _load_seats(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DataSourceError(
                f"Не удалось прочитать файл мест {path.name!r}."
            ) from error

        if isinstance(payload, dict) and "seats_by_file" in payload:
            payload = payload["seats_by_file"]
        if not isinstance(payload, dict):
            raise DataSourceError(
                f"Файл мест {path.name!r} должен содержать объект "
                "{имя_файла: число_мест}."
            )

        seats: dict[str, int] = {}
        for file_name, value in payload.items():
            if not isinstance(file_name, str):
                raise DataSourceError(
                    f"Ключ в файле мест {path.name!r} должен быть строкой."
                )
            try:
                seats_count = int(value)
            except (TypeError, ValueError) as error:
                raise DataSourceError(
                    f"Число мест для {file_name!r} задано некорректно."
                ) from error
            if seats_count < 1:
                raise DataSourceError(
                    f"Число мест для {file_name!r} должно быть >= 1."
                )
            seats[file_name] = seats_count
        self._seats = seats

    def _load_file(self, path: Path) -> None:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as source_file:
                reader = csv.DictReader(source_file, delimiter=";")
                columns = reader.fieldnames
                if not columns or APPLICANT_CODE_COLUMN not in columns:
                    raise DataSourceError(
                        f"В {path.name!r} отсутствует колонка "
                        f"{APPLICANT_CODE_COLUMN!r}."
                    )

                metadata = parse_source_metadata(path, columns)
                confirmation_column = (
                    "Подано согласие"
                    if "Подано согласие" in columns
                    else "Наличие договора"
                    if "Наличие договора" in columns
                    else ""
                )
                members: list[ListMember] = []

                for row_number, row in enumerate(reader, start=2):
                    raw_code = row.pop(APPLICANT_CODE_COLUMN, "")
                    try:
                        code = normalize_code(raw_code)
                    except ValueError as error:
                        raise DataSourceError(
                            f"Некорректный код в {path.name!r}, строка {row_number}."
                        ) from error

                    entry = RankingEntry(
                        source=metadata,
                        position=_parse_int(row.get("Порядковый номер")),
                        priority=_parse_int(row.get("Приоритет конкурса")),
                        total_score=(row.get("Сумма баллов") or "—").strip(),
                        exam_scores=(row.get("Баллы за ВИ") or "—").strip(),
                        individual_score=(row.get("Баллы за ИД") or "—").strip(),
                        status=(row.get("Статус") or "Не указан").strip(),
                        confirmation_label=confirmation_column or "Подтверждение",
                        confirmation_value=(
                            row.get(confirmation_column) or "—"
                        ).strip()
                        if confirmation_column
                        else "—",
                        selected_at=(
                            row.get("Дата выбора конкурсной группы по Москве") or "—"
                        ).strip(),
                    )
                    digest = self._digest(code)
                    self._entries.setdefault(digest, []).append(entry)
                    members.append(ListMember(digest=digest, entry=entry))
                    self.entry_count += 1
        except (OSError, UnicodeError, csv.Error) as error:
            raise DataSourceError(f"Не удалось прочитать {path.name!r}.") from error

        members.sort(
            key=lambda member: (
                member.entry.position is None,
                member.entry.position or 0,
            )
        )
        self._lists[path.name] = members
        self.source_count += 1

    def _seats_for(self, entry: RankingEntry) -> int | None:
        return self._seats.get(entry.source.file_name)

    def _pass_confidence(self, entry: RankingEntry) -> str | None:
        """Return confidence that the applicant will take a seat on this list."""
        if not _is_active(entry) or entry.position is None:
            return None
        if _is_confirmed(entry):
            return "likely"
        seats = self._seats_for(entry)
        if seats is None:
            return "possible"
        if entry.position <= seats:
            return "likely"
        return None

    def _preferred_destination(
        self, current: RankingEntry, other: RankingEntry
    ) -> tuple[str, str] | None:
        """Return (reason, confidence) if `other` is preferred over `current`."""
        if other.source.file_name == current.source.file_name:
            return None
        if not _is_active(other):
            return None

        confidence = self._pass_confidence(other)
        if confidence is None:
            return None

        if _is_confirmed(other) and not _is_confirmed(current):
            if (
                current.priority is None
                or other.priority is None
                or other.priority <= current.priority
            ):
                return "confirmed_elsewhere", "likely"

        if (
            current.source.list_type == "Платное обучение"
            and other.source.list_type == "Бюджет"
        ):
            return "budget_over_paid", confidence

        if (
            current.priority is not None
            and other.priority is not None
            and other.priority < current.priority
        ):
            return "higher_priority", confidence

        return None

    def _best_leave_reason(
        self, current: RankingEntry, digest: bytes
    ) -> tuple[str, str, RankingEntry] | None:
        best: tuple[str, str, RankingEntry] | None = None
        reason_rank = {
            "confirmed_elsewhere": 0,
            "budget_over_paid": 1,
            "higher_priority": 2,
        }
        confidence_rank = {"likely": 0, "possible": 1}

        for other in self._entries.get(digest, ()):
            preferred = self._preferred_destination(current, other)
            if preferred is None:
                continue
            reason, confidence = preferred
            candidate = (reason, confidence, other)
            if best is None:
                best = candidate
                continue
            best_key = (
                confidence_rank[best[1]],
                reason_rank[best[0]],
                best[2].priority if best[2].priority is not None else 10**9,
                best[2].position if best[2].position is not None else 10**9,
            )
            candidate_key = (
                confidence_rank[confidence],
                reason_rank[reason],
                other.priority if other.priority is not None else 10**9,
                other.position if other.position is not None else 10**9,
            )
            if candidate_key < best_key:
                best = candidate
        return best

    def analyze_cascade(
        self, digest: bytes, current: RankingEntry
    ) -> dict[str, object]:
        members = self._lists.get(current.source.file_name, [])
        if current.position is None:
            return {
                "competitors_above": 0,
                "likely_leavers": 0,
                "possible_leavers": 0,
                "effective_position_likely": None,
                "effective_position_possible": None,
                "seats": self._seats_for(current),
                "within_seats_official": None,
                "within_seats_likely": None,
                "within_seats_possible": None,
                "reasons": [],
                "notes": [
                    "Для этой строки нет порядкового номера, каскад не рассчитан."
                ],
            }

        competitors_above = 0
        likely_leavers = 0
        possible_leavers = 0
        reason_counts: dict[str, dict[str, int]] = {
            "likely": {},
            "possible": {},
        }

        for member in members:
            if member.digest == digest:
                continue
            if member.entry.position is None or member.entry.position >= current.position:
                continue
            if not _is_active(member.entry):
                continue

            competitors_above += 1
            leave = self._best_leave_reason(member.entry, member.digest)
            if leave is None:
                continue
            reason, confidence, _destination = leave
            if confidence == "likely":
                likely_leavers += 1
            else:
                possible_leavers += 1
            reason_counts[confidence][reason] = (
                reason_counts[confidence].get(reason, 0) + 1
            )

        effective_likely = max(1, current.position - likely_leavers)
        effective_possible = max(
            1, current.position - likely_leavers - possible_leavers
        )
        seats = self._seats_for(current)

        def within_seats(position: int | None) -> bool | None:
            if seats is None or position is None:
                return None
            return position <= seats

        reasons: list[dict[str, object]] = []
        for confidence in ("likely", "possible"):
            for reason, count in sorted(reason_counts[confidence].items()):
                reasons.append(
                    {
                        "confidence": confidence,
                        "reason": reason,
                        "label": REASON_LABELS[reason],
                        "count": count,
                    }
                )

        notes: list[str] = []
        if seats is None:
            notes.append(
                "Число мест для этой таблицы не задано в seats.json — "
                "оценка «возможно» использует эвристику без жёсткой квоты."
            )
        if possible_leavers:
            notes.append(
                "Часть уходов выше помечена как «возможно»: нет подтверждения "
                "или не задана квота на целевом конкурсе."
            )

        return {
            "competitors_above": competitors_above,
            "likely_leavers": likely_leavers,
            "possible_leavers": possible_leavers,
            "effective_position_likely": effective_likely,
            "effective_position_possible": effective_possible,
            "seats": seats,
            "within_seats_official": within_seats(current.position),
            "within_seats_likely": within_seats(effective_likely),
            "within_seats_possible": within_seats(effective_possible),
            "reasons": reasons,
            "notes": notes,
        }

    @staticmethod
    def pass_verdict(entry: RankingEntry, cascade: dict[str, object]) -> str:
        """Classify the chance to be admitted on this list.

        guaranteed — official position already fits the quota;
        likely / possible — fits only after likely / possible departures above;
        no — stays outside the quota even in the most optimistic scenario;
        unknown — seats or position are missing, or the row is not active.
        """
        if not _is_active(entry) or entry.position is None:
            return "unknown"
        if cascade.get("seats") is None:
            return "unknown"
        if cascade.get("within_seats_official") is True:
            return "guaranteed"
        if cascade.get("within_seats_likely") is True:
            return "likely"
        if cascade.get("within_seats_possible") is True:
            return "possible"
        if cascade.get("within_seats_possible") is False:
            return "no"
        return "unknown"

    def analyze_own_priorities(
        self, digest: bytes, entries: list[RankingEntry]
    ) -> list[dict[str, object]]:
        """Explain where the applicant themselves are likely to stay."""
        insights: list[dict[str, object]] = []
        for current in entries:
            preferred: list[dict[str, object]] = []
            for other in entries:
                result = self._preferred_destination(current, other)
                if result is None:
                    continue
                reason, confidence = result
                preferred.append(
                    {
                        "reason": reason,
                        "label": REASON_LABELS[reason],
                        "confidence": confidence,
                        "destination": _destination_label(other),
                        "destination_position": other.position,
                        "destination_priority": other.priority,
                    }
                )
            preferred.sort(
                key=lambda item: (
                    0 if item["confidence"] == "likely" else 1,
                    item["destination_priority"]
                    if item["destination_priority"] is not None
                    else 10**9,
                )
            )
            insights.append(
                {
                    "source_file": current.source.file_name,
                    "may_leave_for": preferred,
                }
            )
        return insights

    def search(self, applicant_code: object) -> list[RankingEntry]:
        code = normalize_code(applicant_code)
        entries = self._entries.get(self._digest(code), ())
        return sorted(
            entries,
            key=lambda entry: (
                entry.status != "Участвуете в конкурсе",
                entry.position is None,
                entry.position or 0,
                entry.source.program,
            ),
        )

    def public_result(self, applicant_code: object) -> dict[str, object]:
        code = normalize_code(applicant_code)
        digest = self._digest(code)
        entries = self.search(code)
        positions = [
            entry.position for entry in entries if entry.position is not None
        ]
        active_count = sum(
            entry.status == "Участвуете в конкурсе" for entry in entries
        )
        latest_snapshot = max(
            (entry.source.snapshot_at for entry in entries), default=None
        )

        public_entries: list[dict[str, object]] = []
        effective_positions: list[int] = []
        total_likely_leavers = 0
        total_possible_leavers = 0
        own_priority = {
            item["source_file"]: item["may_leave_for"]
            for item in self.analyze_own_priorities(digest, entries)
        }

        verdict_counts = {
            "guaranteed": 0,
            "likely": 0,
            "possible": 0,
            "no": 0,
            "unknown": 0,
        }

        for entry in entries:
            cascade = self.analyze_cascade(digest, entry)
            total_likely_leavers += int(cascade["likely_leavers"])
            total_possible_leavers += int(cascade["possible_leavers"])
            if cascade["effective_position_possible"] is not None:
                effective_positions.append(
                    int(cascade["effective_position_possible"])
                )
            payload = entry.to_public_dict()
            payload["cascade"] = cascade
            payload["own_priority"] = own_priority.get(entry.source.file_name, [])
            verdict = self.pass_verdict(entry, cascade)
            payload["pass_verdict"] = verdict
            verdict_counts[verdict] += 1
            public_entries.append(payload)

        return {
            "found": bool(entries),
            "summary": {
                "matches": len(entries),
                "active": active_count,
                "best_position": min(positions) if positions else None,
                "best_effective_position": (
                    min(effective_positions) if effective_positions else None
                ),
                "likely_leavers_total": total_likely_leavers,
                "possible_leavers_total": total_possible_leavers,
                "latest_snapshot": latest_snapshot,
                "verdicts": verdict_counts,
            },
            "entries": public_entries,
        }
