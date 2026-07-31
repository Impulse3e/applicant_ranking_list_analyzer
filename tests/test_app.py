from pathlib import Path

import pytest

from app import create_app


SECRET = b"test-secret-that-is-at-least-32-bytes-long"
APPLICANT_CODE = "1234567"


@pytest.fixture()
def data_directory(tmp_path: Path) -> Path:
    path = tmp_path / "Программная_инженерия.2026-07-30_20-05-06.csv"
    path.write_text(
        "\n".join(
            [
                (
                    '"Порядковый номер";"Приоритет конкурса";"Наличие договора";'
                    '"Сумма баллов";"Баллы за ВИ";"Баллы за ИД";"Статус";'
                    '"Код поступающего";"Дата выбора конкурсной группы по Москве"'
                ),
                (
                    f'"3";"1";"Да";"295";"98 97 95";"5";'
                    f'"Участвуете в конкурсе";"{APPLICANT_CODE}";'
                    '"30.07.2026 в 12:00"'
                ),
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def client(data_directory: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIRECTORY": data_directory,
            "HMAC_SECRET": SECRET,
            "SEARCH_RATE_LIMIT": 100,
        }
    )
    return app.test_client()


def test_page_does_not_render_applicant_codes(client):
    response = client.get("/")

    assert response.status_code == 200
    assert APPLICANT_CODE.encode() not in response.data
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Content-Security-Policy"].startswith(
        "default-src 'self'"
    )


def test_search_returns_only_matching_public_fields(client):
    response = client.post(
        "/api/search",
        json={"applicant_code": APPLICANT_CODE},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["found"] is True
    assert payload["summary"]["matches"] == 1
    assert payload["entries"][0]["position"] == 3
    assert APPLICANT_CODE.encode() not in response.data
    assert "Код поступающего".encode() not in response.data


def test_csv_sources_are_not_publicly_served(client):
    response = client.get(
        "/data/Программная_инженерия.2026-07-30_20-05-06.csv"
    )

    assert response.status_code == 404


def test_rejects_invalid_search_input(client):
    response = client.post("/api/search", json={"applicant_code": "not-a-code"})

    assert response.status_code == 400
    assert "ошиб" not in response.get_json()["error"].lower()


def test_rate_limits_repeated_searches(data_directory: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIRECTORY": data_directory,
            "HMAC_SECRET": SECRET,
            "SEARCH_RATE_LIMIT": 1,
            "SEARCH_RATE_WINDOW": 60,
        }
    )
    client = app.test_client()

    first = client.post("/api/search", json={"applicant_code": APPLICANT_CODE})
    second = client.post("/api/search", json={"applicant_code": APPLICANT_CODE})

    assert first.status_code == 200
    assert second.status_code == 429
