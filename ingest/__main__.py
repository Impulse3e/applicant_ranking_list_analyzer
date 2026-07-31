from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingest.adapters import ADAPTERS, get_adapter
from ingest.http_util import HttpClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Скачать конкурсные списки вузов в канонический CSV для data/."
        )
    )
    parser.add_argument(
        "--university",
        "-u",
        action="append",
        dest="universities",
        help=(
            "Код вуза: "
            + ", ".join(sorted(ADAPTERS))
            + ". Можно указать несколько раз. По умолчанию — все доступные."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data"),
        help="Каталог для CSV (по умолчанию data/).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Пауза между HTTP-запросами в секундах.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Отключить проверку TLS (нужно для части сайтов, например МПУ).",
    )
    parser.add_argument(
        "--max-lists",
        type=int,
        default=None,
        help="Ограничить число списков на вуз (удобно для smoke-теста).",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help=(
            "HTTP(S) proxy, например http://127.0.0.1:12334 (Hiddify mixed-port). "
            "Нужен для МИРЭА, если .ru не должен идти Direct."
        ),
    )
    args = parser.parse_args(argv)

    selected = args.universities or [
        code for code in ADAPTERS if code not in {"mipt", "mirea"}
    ]
    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Выходной каталог: {output_dir.resolve()}")
    print(f"Вузы: {', '.join(selected)}")
    if args.proxy:
        print(f"Прокси: {args.proxy}")

    needs_insecure = args.insecure or any(code == "mospoly" for code in selected)
    with HttpClient(
        delay_seconds=args.delay,
        verify=not needs_insecure,
        proxy=args.proxy,
    ) as client:
        for code in selected:
            adapter = get_adapter(code)
            adapter.max_lists = args.max_lists
            print(f"\n=== {adapter.name} ({code}) ===")
            report = adapter.collect(client, output_dir)
            for note in report.skipped[:20]:
                print(f"  skip  {note}", flush=True)
            if len(report.skipped) > 20:
                print(f"  skip  ... и ещё {len(report.skipped) - 20}", flush=True)
            for error in report.errors:
                print(f"  ERROR {error}", flush=True)
            print(
                f"Итого: сохранено {len(report.saved)}, "
                f"пропущено {len(report.skipped)}, ошибок {len(report.errors)}",
                flush=True,
            )

    removed = prune_seats(output_dir)
    if removed:
        print(f"\nseats.json: удалено {removed} записей об исчезнувших таблицах.")

    return 0


def prune_seats(output_dir: Path) -> int:
    """Drop seats.json entries whose CSV is no longer on disk."""
    seats_path = output_dir / "seats.json"
    if not seats_path.is_file():
        return 0
    try:
        payload = json.loads(seats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    seats = payload.get("seats_by_file")
    if not isinstance(seats, dict):
        return 0
    alive = {name: value for name, value in seats.items() if (output_dir / name).is_file()}
    removed = len(seats) - len(alive)
    if removed:
        payload["seats_by_file"] = alive
        seats_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return removed


if __name__ == "__main__":
    raise SystemExit(main())
