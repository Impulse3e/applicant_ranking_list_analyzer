from __future__ import annotations

from ingest.adapters.hse import HseAdapter
from ingest.adapters.misis import MisisAdapter
from ingest.adapters.mospoly import MospolyAdapter
from ingest.adapters.mtuci import MtuciAdapter
from ingest.adapters.stankin import StankinAdapter
from ingest.adapters.stubs import MiptAdapter, MireaAdapter
from ingest.base import BaseAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    MisisAdapter.code: MisisAdapter,
    MtuciAdapter.code: MtuciAdapter,
    StankinAdapter.code: StankinAdapter,
    MospolyAdapter.code: MospolyAdapter,
    HseAdapter.code: HseAdapter,
    MiptAdapter.code: MiptAdapter,
    MireaAdapter.code: MireaAdapter,
}


def get_adapter(code: str) -> BaseAdapter:
    try:
        adapter_cls = ADAPTERS[code]
    except KeyError as error:
        known = ", ".join(sorted(ADAPTERS))
        raise KeyError(f"Неизвестный адаптер {code!r}. Доступны: {known}") from error
    return adapter_cls()
