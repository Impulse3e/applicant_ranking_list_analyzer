import json
from pathlib import Path

import pytest

from ranking import DataSourceError, RankingIndex, normalize_code


SECRET = b"test-secret-that-is-at-least-32-bytes-long"


def write_list(
    directory: Path,
    name: str,
    confirmation_column: str,
    rows: list[dict[str, str]],
) -> Path:
    path = directory / name
    columns = [
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
    lines = [";".join(f'"{column}"' for column in columns)]
    for row in rows:
        lines.append(";".join(f'"{row.get(column, "")}"' for column in columns))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def sample_row(code: str, position: str = "7") -> dict[str, str]:
    return {
        "Порядковый номер": position,
        "Приоритет конкурса": "2",
        "Подано согласие": "Электронное",
        "Наличие договора": "Да",
        "Сумма баллов": "287",
        "Баллы за ВИ": "90 94 95",
        "Баллы за ИД": "8",
        "Статус": "Участвуете в конкурсе",
        "Код поступающего": code,
        "Дата выбора конкурсной группы по Москве": "30.07.2026 в 12:00",
    }


def test_searches_all_lists_without_returning_applicant_code(tmp_path: Path):
    write_list(
        tmp_path,
        "Анализ_данных.2026-07-27_10-59-41.csv",
        "Подано согласие",
        [sample_row("1234567", "12"), sample_row("7654321", "18")],
    )
    write_list(
        tmp_path,
        "Инженерия_данных.2026-07-30_20-05-06.csv",
        "Наличие договора",
        [sample_row("1234567", "4")],
    )

    index = RankingIndex.from_directory(tmp_path, SECRET)
    result = index.public_result("1234567")
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["found"] is True
    assert result["summary"]["matches"] == 2
    assert result["summary"]["active"] == 2
    assert result["summary"]["best_position"] == 4
    assert result["summary"]["latest_snapshot"] == "2026-07-30T20:05:06"
    assert [entry["position"] for entry in result["entries"]] == [4, 12]
    assert "1234567" not in serialized
    assert "7654321" not in serialized
    assert "Код поступающего" not in serialized


def test_unknown_code_returns_empty_public_result(tmp_path: Path):
    write_list(
        tmp_path,
        "Анализ_данных.2026-07-27_10-59-41.csv",
        "Подано согласие",
        [sample_row("1234567")],
    )

    result = RankingIndex.from_directory(tmp_path, SECRET).public_result("9999999")

    assert result["found"] is False
    assert result["entries"] == []
    assert result["summary"]["matches"] == 0


@pytest.mark.parametrize("value", ["", "12 34", "abc", "-1", "1.5", "1" * 21])
def test_rejects_invalid_codes(value: str):
    with pytest.raises(ValueError):
        normalize_code(value)


def test_rejects_file_without_applicant_code(tmp_path: Path):
    path = tmp_path / "Список.2026-07-30_20-05-06.csv"
    path.write_text('"Порядковый номер";"Статус"\n"1";"Участвует"', encoding="utf-8")

    with pytest.raises(DataSourceError):
        RankingIndex.from_directory(tmp_path, SECRET)


def _row(
    code: str,
    position: str,
    priority: str,
    *,
    consent: str = "—",
    contract: str = "—",
    status: str = "Участвуете в конкурсе",
) -> dict[str, str]:
    row = sample_row(code, position)
    row["Приоритет конкурса"] = priority
    row["Подано согласие"] = consent
    row["Наличие договора"] = contract
    row["Статус"] = status
    return row


def test_cascade_counts_budget_leavers_above_on_paid_list(tmp_path: Path):
    write_list(
        tmp_path,
        "ИИ.2026-07-30_20-00-00.csv",
        "Подано согласие",
        [
            _row("1000001", "1", "1", consent="Электронное"),
            _row("1000002", "2", "1"),
            _row("2000000", "10", "2"),
        ],
    )
    write_list(
        tmp_path,
        "ИИ.2026-07-30_20-10-00.csv",
        "Наличие договора",
        [
            _row("1000001", "1", "3"),
            _row("1000002", "2", "2"),
            _row("3000000", "3", "1"),
            _row("2000000", "4", "1"),
        ],
    )
    (tmp_path / "seats.json").write_text(
        json.dumps(
            {
                "seats_by_file": {
                    "ИИ.2026-07-30_20-00-00.csv": 2,
                    "ИИ.2026-07-30_20-10-00.csv": 3,
                }
            }
        ),
        encoding="utf-8",
    )

    result = RankingIndex.from_directory(tmp_path, SECRET).public_result("2000000")
    paid = next(
        entry
        for entry in result["entries"]
        if entry["source"]["list_type"] == "Платное обучение"
    )
    cascade = paid["cascade"]
    serialized = json.dumps(result, ensure_ascii=False)

    assert paid["position"] == 4
    assert cascade["competitors_above"] == 3
    assert cascade["likely_leavers"] == 2
    assert cascade["effective_position_likely"] == 2
    assert cascade["within_seats_official"] is False
    assert cascade["within_seats_likely"] is True
    assert any(item["reason"] == "budget_over_paid" for item in cascade["reasons"])
    assert "1000001" not in serialized
    assert "1000002" not in serialized


def test_cascade_counts_higher_priority_leavers(tmp_path: Path):
    write_list(
        tmp_path,
        "Программа_А.2026-07-30_20-00-00.csv",
        "Подано согласие",
        [
            _row("1000001", "1", "1"),
            _row("2000000", "5", "2"),
        ],
    )
    write_list(
        tmp_path,
        "Программа_Б.2026-07-30_20-00-00.csv",
        "Подано согласие",
        [
            _row("1000001", "1", "3"),
            _row("3000000", "2", "1"),
            _row("2000000", "3", "1"),
        ],
    )
    (tmp_path / "seats.json").write_text(
        json.dumps(
            {
                "seats_by_file": {
                    "Программа_А.2026-07-30_20-00-00.csv": 1,
                    "Программа_Б.2026-07-30_20-00-00.csv": 2,
                }
            }
        ),
        encoding="utf-8",
    )

    result = RankingIndex.from_directory(tmp_path, SECRET).public_result("2000000")
    program_b = next(
        entry
        for entry in result["entries"]
        if entry["source"]["program"] == "Программа Б"
    )
    cascade = program_b["cascade"]

    assert program_b["position"] == 3
    assert cascade["likely_leavers"] == 1
    assert cascade["effective_position_likely"] == 2
    assert any(item["reason"] == "higher_priority" for item in cascade["reasons"])


def test_own_priority_marks_lower_priority_lists(tmp_path: Path):
    write_list(
        tmp_path,
        "Программа_А.2026-07-30_20-00-00.csv",
        "Подано согласие",
        [_row("2000000", "1", "1", consent="Электронное")],
    )
    write_list(
        tmp_path,
        "Программа_Б.2026-07-30_20-00-00.csv",
        "Подано согласие",
        [_row("2000000", "1", "3")],
    )
    (tmp_path / "seats.json").write_text(
        json.dumps(
            {
                "seats_by_file": {
                    "Программа_А.2026-07-30_20-00-00.csv": 1,
                    "Программа_Б.2026-07-30_20-00-00.csv": 1,
                }
            }
        ),
        encoding="utf-8",
    )

    result = RankingIndex.from_directory(tmp_path, SECRET).public_result("2000000")
    program_b = next(
        entry
        for entry in result["entries"]
        if entry["source"]["program"] == "Программа Б"
    )

    assert program_b["own_priority"]
    assert program_b["own_priority"][0]["reason"] == "confirmed_elsewhere"
    assert "Программа А" in program_b["own_priority"][0]["destination"]


def test_possible_leavers_when_seats_unknown(tmp_path: Path):
    write_list(
        tmp_path,
        "ИИ.2026-07-30_20-00-00.csv",
        "Подано согласие",
        [_row("1000001", "50", "1")],
    )
    write_list(
        tmp_path,
        "ИИ.2026-07-30_20-10-00.csv",
        "Наличие договора",
        [
            _row("1000001", "1", "2"),
            _row("2000000", "2", "1"),
        ],
    )

    result = RankingIndex.from_directory(tmp_path, SECRET).public_result("2000000")
    paid = next(
        entry
        for entry in result["entries"]
        if entry["source"]["list_type"] == "Платное обучение"
    )

    assert paid["cascade"]["likely_leavers"] == 0
    assert paid["cascade"]["possible_leavers"] == 1
    assert paid["cascade"]["effective_position_possible"] == 1
