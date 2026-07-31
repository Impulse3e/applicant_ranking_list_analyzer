from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SAFE_NAME = re.compile(r"[^\w\u0400-\u04FF\-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CanonicalRow:
    position: str
    priority: str
    confirmation: str
    total_score: str
    exam_scores: str
    individual_score: str
    status: str
    applicant_code: str
    selected_at: str = "—"


def safe_filename_part(value: str, max_bytes: int = 80) -> str:
    cleaned = SAFE_NAME.sub("_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "unknown"
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= max_bytes:
        return cleaned
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip("_")
    return truncated or "unknown"


def build_output_path(
    output_dir: Path,
    university: str,
    program: str,
    is_budget: bool,
    snapshot_at: datetime | None = None,
) -> Path:
    stamp = (snapshot_at or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    list_type = "бюджет" if is_budget else "платное"
    uni = safe_filename_part(university, 40)
    typ = safe_filename_part(list_type, 20)
    # Linux NAME_MAX is 255 bytes; leave a margin for UTF-8 Cyrillic.
    overhead = len(f"{uni}____{typ}.{stamp}.csv".encode("utf-8"))
    prog = safe_filename_part(program, max(32, 240 - overhead))
    return output_dir / f"{uni}__{prog}__{typ}.{stamp}.csv"


def write_canonical_csv(
    path: Path,
    rows: list[CanonicalRow],
    *,
    is_budget: bool,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    confirmation_column = "Подано согласие" if is_budget else "Наличие договора"
    fieldnames = [
        "Порядковый номер",
        "Приоритет конкурса",
        confirmation_column,
        "Сумма баллов",
        "Баллы за ВИ",
        "Баллы за ИД",
        "Статус",
        "Код поступающего",
        "Дата выбора конкурсной группы по Москве",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter=";",
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "Порядковый номер": row.position,
                    "Приоритет конкурса": row.priority,
                    confirmation_column: row.confirmation,
                    "Сумма баллов": row.total_score,
                    "Баллы за ВИ": row.exam_scores,
                    "Баллы за ИД": row.individual_score,
                    "Статус": row.status,
                    "Код поступающего": row.applicant_code,
                    "Дата выбора конкурсной группы по Москве": row.selected_at,
                }
            )
    return path


def dash(value: object | None) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text and text.lower() not in {"none", "null", "nan"} else "—"
