"""Fetch official seat quotas and merge them into data/seats.json.

Sources (2026 campaign):
- HSE: https://ba.hse.ru/kolmest
- MTUCI: https://abitur.mtuci.ru/bachelor/
- STANKIN: https://priem.stankin.ru/bakalavriatispetsialitet/training_programs/
- MISIS: progress «План приема» pages
- MosPoly: appendix 2.4 PDF (Moscow campus)
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ingest.base import _merge_seats

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


@dataclass(frozen=True)
class SeatOffer:
    """Seats for one program under different contest kinds."""

    key: str  # normalized program key / specialty code
    title: str
    main: int | None = None
    special: int | None = None
    separate: int | None = None
    target: int | None = None
    paid: int | None = None
    total_budget: int | None = None


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "—", "–"}:
        return None
    match = re.search(r"\d+", text.replace("\xa0", " "))
    return int(match.group(0)) if match else None


def _code_from_name(name: str) -> str | None:
    match = re.search(r"(\d{2})[._](\d{2})[._](\d{2})(?:[._](\d{2}))?", name)
    if not match:
        return None
    parts = [match.group(1), match.group(2), match.group(3)]
    if match.group(4):
        parts.append(match.group(4))
    return ".".join(parts)


def _kind_from_filename(name: str) -> str:
    lower = name.lower()
    if "__платное." in lower or "__платное" in lower or "полное_возмещение" in lower:
        if "ино" in lower or "иностран" in lower:
            return "paid_foreign"
        return "paid"
    if "особо" in lower or "_оп_" in name or "особое_право" in lower:
        return "special"
    if "отдельн" in lower or "_отд_" in lower or "spetsial" in lower:
        return "separate"
    if "целев" in lower or "_цп_" in lower:
        return "target"
    if "общий_конкурс" in lower or "бюджетные_места" in lower:
        return "main"
    if "__бюджет" in lower or "бюджетная_основа" in lower:
        return "main"
    return "unknown"


def _program_blob(name: str) -> str:
    """Filename middle part without university prefix and type/stamp suffix."""
    stem = name
    if "__" in stem:
        stem = stem.split("__", 1)[1]
    stem = re.sub(r"__(бюджет|платное)\.\d{4}-\d{2}-\d{2}.*$", "", stem, flags=re.I)
    stem = re.sub(r"\.\d{4}-\d{2}-\d{2}.*$", "", stem)
    return stem


def _hse_program_needle(filename: str) -> str:
    blob = _program_blob(filename)
    # Cut at financing / place-type markers used in ingest filenames.
    blob = re.split(
        r"_О_[БКЦПДВбк]|_Бюджетные_места|_ОП_|_Отд_|_ЦП_|_Москва|_С_оплатой",
        blob,
        maxsplit=1,
    )[0]
    return _norm(blob)


def _get(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
    response = client.get(url, headers=HEADERS, follow_redirects=True, **kwargs)
    response.raise_for_status()
    return response


def fetch_hse(client: httpx.Client) -> list[SeatOffer]:
    html = _get(client, "https://ba.hse.ru/kolmest").text
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []
    offers: list[SeatOffer] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 7:
            continue
        title = cells[0]
        if not title or title.upper().startswith("НАПРАВЛЕНИЕ") or "форма обучения" in title.lower():
            continue
        if title.lower().startswith("итого") or title.lower().startswith("всего"):
            continue
        total = _first_int(cells[2])
        special = _first_int(cells[3])
        target = _first_int(cells[4])
        separate = _first_int(cells[5])
        paid = _first_int(cells[6])
        if total is None and paid is None:
            continue
        special = special or 0
        target = target or 0
        separate = separate or 0
        main = None
        if total is not None:
            main = max(0, total - special - target - separate)
        offers.append(
            SeatOffer(
                key=_norm(title),
                title=title,
                main=main,
                special=special or None,
                separate=separate or None,
                target=target or None,
                paid=paid,
                total_budget=total,
            )
        )
    return offers


def fetch_mtuci(client: httpx.Client) -> list[SeatOffer]:
    html = _get(client, "https://abitur.mtuci.ru/bachelor/").text
    soup = BeautifulSoup(html, "lxml")
    offers: list[SeatOffer] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 6:
                continue
            title = cells[0]
            code = _code_from_name(title)
            if code is None:
                continue
            total = _first_int(cells[2])
            special = _first_int(cells[3]) or 0
            separate = _first_int(cells[4]) or 0
            target = _first_int(cells[5]) or 0
            paid = _first_int(cells[6]) if len(cells) > 6 else None
            main = None if total is None else max(0, total - special - separate - target)
            offers.append(
                SeatOffer(
                    key=code,
                    title=title,
                    main=main,
                    special=special or None,
                    separate=separate or None,
                    target=target or None,
                    paid=paid,
                    total_budget=total,
                )
            )
    return offers


def fetch_stankin(client: httpx.Client) -> list[SeatOffer]:
    html = _get(
        client,
        "https://priem.stankin.ru/bakalavriatispetsialitet/training_programs/",
    ).text
    soup = BeautifulSoup(html, "lxml")
    offers: list[SeatOffer] = []
    # Compact rows look like:
    # "09.03.01 ... 70 7 7 11 23"
    for tr in soup.find_all("tr"):
        text = re.sub(r"\s+", " ", tr.get_text(" ", strip=True))
        match = re.search(
            r"(\d{2}\.\d{2}\.\d{2}(?:\.\d{2})?)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
            text,
        )
        if not match:
            continue
        code = match.group(1)
        title = match.group(2).strip()
        # Drop trailing score/cost noise before the five trailing integers.
        title = re.sub(r"(Очная|Р \+|руб/сем).*$", "", title).strip() or code
        total, special, separate, target, paid = map(int, match.groups()[2:])
        offers.append(
            SeatOffer(
                key=code,
                title=title,
                main=max(0, total - special - separate - target),
                special=special,
                separate=separate,
                target=target,
                paid=paid,
                total_budget=total,
            )
        )
    return offers


def fetch_misis(client: httpx.Client) -> list[SeatOffer]:
    base = "https://misis.ru"
    pages = {
        "main": "/applicants/admission/progress/baccalaureate-and-specialties/kolichestvopostupayush_ihipodan/obsh_iikonkurs/",
        "special": "/applicants/admission/progress/baccalaureate-and-specialties/kolichestvopostupayush_ihipodan/osobayakvota/",
        "target": "/applicants/admission/progress/baccalaureate-and-specialties/kolichestvopostupayush_ihipodan/tselevayakvota/",
        "separate": "/applicants/admission/progress/baccalaureate-and-specialties/kolichestvopostupayush_ihipodan/spetsial_nayakvota/",
        "paid": "/applicants/admission/progress/baccalaureate-and-specialties/applications-extrabudgetary/",
    }
    by_code: dict[str, dict[str, Any]] = {}

    def ingest(kind: str, html: str) -> None:
        soup = BeautifulSoup(html, "lxml")
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < 4:
                    continue
                contest = cells[2] if len(cells) >= 4 else ""
                if any(token in contest.lower() for token in ("филиал", "вф ", "сти ", "нф ")):
                    continue
                if any(
                    token in " ".join(cells[:2]).lower()
                    for token in ("филиал", "выксун", "новотроиц", "душанбе", "сти ниту")
                ):
                    continue
                code = _code_from_name(contest)
                plan = _first_int(cells[3])
                if code is None or plan is None:
                    continue
                entry = by_code.setdefault(code, {"title": contest, "code": code})
                entry[kind] = plan

    for kind, path in pages.items():
        ingest(kind, _get(client, base + path).text)

    offers: list[SeatOffer] = []
    for code, entry in by_code.items():
        offers.append(
            SeatOffer(
                key=code,
                title=str(entry["title"]),
                main=entry.get("main"),
                special=entry.get("special"),
                separate=entry.get("separate"),
                target=entry.get("target"),
                paid=entry.get("paid"),
            )
        )
    return offers


def fetch_mospoly(client: httpx.Client) -> list[SeatOffer]:
    from pypdf import PdfReader

    pdf_url = (
        "https://mospolytech.ru/upload/iblock/884/"
        "h0hkkxa451lhjldxame90d202peiubnv/2.4_Moskva.pdf"
    )
    with httpx.Client(timeout=120.0, verify=False, headers=HEADERS) as insecure:
        response = insecure.get(pdf_url, follow_redirects=True)
        response.raise_for_status()
    reader = PdfReader(BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    offers: list[SeatOffer] = []
    # Lines like: 08.03.01.01 Промышленное ... 34 126
    # or with ochno-zaochnaya: 61 109 15 135
    pattern = re.compile(
        r"(?m)^(\d{2}\.\d{2}\.\d{2}(?:\.\d{2})?)\s*(.*?)\s+(\d+)(?:\s+(\d+))?(?:\s+(\d+)\s+(\d+))?\s*$"
    )
    # PDF extraction often joins lines; work on flattened chunks around codes.
    for match in re.finditer(
        r"(\d{2}\.\d{2}\.\d{2}(?:\.\d{2})?)([^\d]{3,160}?)(\d{1,3})(?:\s+(\d{1,3}))?(?:\s+(\d{1,3})\s+(\d{1,3}))?",
        text,
    ):
        code = match.group(1)
        title = re.sub(r"\s+", " ", match.group(2)).strip(" -–—")
        if len(title) < 3:
            continue
        budget = int(match.group(3))
        paid = int(match.group(4)) if match.group(4) else None
        # Prefer ochnaya pair; if four numbers, first two are ochnaya.
        if match.group(5) and match.group(6):
            budget = int(match.group(3))
            paid = int(match.group(4))
        offers.append(
            SeatOffer(
                key=code,
                title=title[:120],
                main=budget,
                paid=paid,
                total_budget=budget,
            )
        )
    # Deduplicate by code keeping first rich hit.
    unique: dict[str, SeatOffer] = {}
    for offer in offers:
        unique.setdefault(offer.key, offer)
    return list(unique.values())


def _pick_seats(offer: SeatOffer, kind: str) -> int | None:
    if kind == "main":
        return offer.main if offer.main is not None else offer.total_budget
    if kind == "special":
        return offer.special
    if kind == "separate":
        return offer.separate
    if kind == "target":
        return offer.target
    if kind == "paid":
        return offer.paid
    return None


def _match_offer(
    filename: str, offers: Iterable[SeatOffer], *, by: str
) -> SeatOffer | None:
    blob = _program_blob(filename)
    if by == "code":
        code = _code_from_name(blob)
        if code is None:
            return None
        # Prefer longest matching code (09.03.01.01 over 09.03.01).
        candidates = [o for o in offers if code == o.key or code.startswith(o.key + ".")]
        if not candidates:
            # Also allow offer codes that start with file code.
            candidates = [o for o in offers if o.key == code or o.key.startswith(code + ".")]
        if not candidates:
            return None
        return max(candidates, key=lambda o: (len(o.key), o.main or 0))

    # HSE: fuzzy title match.
    needle = _hse_program_needle(filename)
    if not needle:
        return None

    exact = [o for o in offers if o.key == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return max(exact, key=lambda o: ((o.main or 0), (o.paid or 0)))

    best: SeatOffer | None = None
    best_score = 0.0
    needle_tokens = set(needle.split())
    for offer in offers:
        title = offer.key
        if not title:
            continue
        title_tokens = set(title.split())
        if not title_tokens:
            continue
        # Prefer title that equals needle or is a clean prefix of the filename blob.
        if title == needle:
            score = 1000.0 + len(title)
        elif needle.startswith(title + " ") or needle.startswith(title):
            # "математика" must not steal "прикладная математика".
            if any(
                other.key != title
                and other.key.startswith(title)
                and needle.startswith(other.key)
                for other in offers
            ):
                continue
            score = 800.0 + len(title)
        elif title.startswith(needle + " ") and len(needle_tokens) >= 3:
            score = 500.0 + len(needle)
        else:
            overlap = len(title_tokens & needle_tokens)
            if overlap < 2 and len(needle_tokens) > 1:
                continue
            if overlap == 0:
                continue
            # Reject when the offer is much longer and only shares a generic word.
            if len(title_tokens) >= len(needle_tokens) + 3 and overlap <= 1:
                continue
            coverage = overlap / max(len(title_tokens), len(needle_tokens))
            if coverage < 0.75:
                continue
            score = 100.0 * coverage + overlap
        if score > best_score:
            best = offer
            best_score = score
    return best


def enrich_directory(
    data_dir: Path, *, dry_run: bool = False, replace: bool = False
) -> dict[str, int]:
    stats = {
        "files": 0,
        "matched": 0,
        "written": 0,
        "skipped_existing": 0,
        "unmatched": 0,
    }
    seats_path = data_dir / "seats.json"
    existing: dict[str, int] = {}
    if seats_path.is_file():
        try:
            payload = json.loads(seats_path.read_text(encoding="utf-8"))
            raw = payload.get("seats_by_file", payload)
            if isinstance(raw, dict):
                existing = {
                    str(k): int(v)
                    for k, v in raw.items()
                    if str(v).isdigit() or isinstance(v, int)
                }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            existing = {}

    with httpx.Client(timeout=90.0) as client:
        catalogs = {
            "ВШЭ": ("title", fetch_hse(client)),
            "МТУСИ": ("code", fetch_mtuci(client)),
            "СТАНКИН": ("code", fetch_stankin(client)),
            "МИСИС": ("code", fetch_misis(client)),
            "МПУ": ("code", fetch_mospoly(client)),
        }

    print("Каталоги мест:")
    for uni, (_mode, offers) in catalogs.items():
        print(f"  {uni}: {len(offers)}")

    updates: dict[str, int] = {}
    unmatched_samples: list[str] = []
    for path in sorted(data_dir.glob("*.csv")):
        stats["files"] += 1
        uni = path.name.split("__", 1)[0]
        if uni not in catalogs:
            continue
        if path.name in existing and not replace:
            stats["skipped_existing"] += 1
            continue
        mode, offers = catalogs[uni]
        kind = _kind_from_filename(path.name)
        offer = _match_offer(path.name, offers, by=mode)
        if offer is None:
            stats["unmatched"] += 1
            if len(unmatched_samples) < 12:
                unmatched_samples.append(path.name)
            continue
        seats = _pick_seats(offer, kind)
        if seats is None or seats <= 0:
            stats["unmatched"] += 1
            if len(unmatched_samples) < 12:
                unmatched_samples.append(
                    f"{path.name} (kind={kind}, offer={offer.title})"
                )
            continue
        stats["matched"] += 1
        updates[path.name] = int(seats)

    if dry_run:
        print(f"dry-run: нашлось бы {len(updates)} записей")
    else:
        for name, seats in updates.items():
            _merge_seats(seats_path, name, seats)
            stats["written"] += 1

    print(
        f"Файлов: {stats['files']}, уже было: {stats['skipped_existing']}, "
        f"сопоставлено: {stats['matched']}, записано: {stats['written']}, "
        f"не найдено: {stats['unmatched']}"
    )
    if unmatched_samples:
        print("Примеры без квоты:")
        for sample in unmatched_samples:
            print(f"  - {sample}")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Подтянуть квоты мест в seats.json")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Перезаписать уже существующие записи для вузов из веб-каталогов.",
    )
    args = parser.parse_args(argv)
    enrich_directory(args.data, dry_run=args.dry_run, replace=args.replace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
