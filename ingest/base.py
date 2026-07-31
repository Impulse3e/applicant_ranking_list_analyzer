from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ingest.http_util import HttpClient
from ingest.schema import CanonicalRow, build_output_path, write_canonical_csv


@dataclass
class FetchResult:
    university: str
    program: str
    is_budget: bool
    rows: list[CanonicalRow]
    source_url: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class AdapterReport:
    university: str
    saved: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BaseAdapter:
    code: str
    name: str
    max_lists: int | None = None

    def fetch_all(self, client: HttpClient) -> list[FetchResult]:
        raise NotImplementedError

    def iter_results(self, client: HttpClient):
        """Yield lists one by one so collect() can write incrementally."""
        yield from self.fetch_all(client)

    def collect(self, client: HttpClient, output_dir: Path) -> AdapterReport:
        report = AdapterReport(university=self.name)
        processed = 0
        try:
            stream = self.iter_results(client)
        except Exception as error:  # noqa: BLE001 - top-level adapter guard
            report.errors.append(str(error))
            return report

        for result in stream:
            if self.max_lists is not None and processed >= self.max_lists:
                break
            processed += 1
            if not result.rows:
                report.skipped.append(f"{result.program}: пустой список")
                continue
            try:
                path = build_output_path(
                    output_dir,
                    result.university,
                    result.program,
                    result.is_budget,
                )
                write_canonical_csv(path, result.rows, is_budget=result.is_budget)
                report.saved.append(path)
                print(f"  saved {path.name}", flush=True)
            except Exception as error:  # noqa: BLE001 - keep collecting other lists
                report.errors.append(f"{result.program}: {error}")
                print(f"  ERROR {result.program}: {error}", flush=True)
        return report
