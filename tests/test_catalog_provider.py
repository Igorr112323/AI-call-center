import json
import time

import pytest

from app.config import AppSettings, CatalogConfig
from app.core.models import OrderItem
from app.core.storage import Storage
from app.integrations.catalog_provider import (
    LocalCatalogProvider, extract_params,
)


@pytest.fixture
def provider(tmp_path):
    storage = Storage(tmp_path / "cat.db")
    storage.replace_catalog([
        {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
         "category": "актуатор", "price": 11500.0, "stock": 14,
         "params_json": json.dumps({"voltage": 24, "stroke_mm": 300})},
        {"sku": "ACT-12-150", "name": "Актуатор линейный 12В, ход 150мм",
         "category": "актуатор", "price": 4200.0, "stock": 60,
         "params_json": json.dumps({"voltage": 12, "stroke_mm": 150})},
        {"sku": "SRV-750-B", "name": "Сервопривод 750Вт с тормозом",
         "category": "сервопривод", "price": 48000.0, "stock": 2,
         "params_json": json.dumps({"power_w": 750})},
    ])
    return LocalCatalogProvider(storage, CatalogConfig())


def test_extract_params_from_speech():
    params = extract_params("нужен актуатор на 24 вольта, ход 300 мм, две штуки")
    assert params["voltage"] == 24
    assert params["stroke_mm"] == 300
    assert params["quantity"] == 2


def test_fuzzy_lookup_matches_engineering_params(provider):
    product = provider.lookup_product("актуатор на 24 вольта 300 мм")
    assert product.found
    assert product.article == "ACT-24-300"
    assert product.price == 11500 and product.stock == 14

    other = provider.lookup_product("актуатор 12В, ход 150")
    assert other.article == "ACT-12-150"


def test_fuzzy_lookup_under_five_milliseconds(provider):
    # warm caches
    provider.lookup_product("актуатор 24В 300мм")
    t0 = time.perf_counter()
    for _ in range(100):
        provider.lookup_product("актуатор 24В 300мм")
    elapsed_ms = (time.perf_counter() - t0) * 1000 / 100
    assert elapsed_ms < 5.0


def test_unknown_params_return_not_found(provider):
    product = provider.lookup_product("актуатор 380В ход 900мм")
    assert product.found is False


def test_sku_substring_is_strong_signal(provider):
    product = provider.lookup_product("есть ли у вас SRV-750-B?")
    assert product.found and product.article == "SRV-750-B"


async def test_standalone_order_numbering(provider):
    item = OrderItem("ACT-24-300", "Актуатор 24В", 1, 11500)
    first = await provider.create_lead_or_order("+79180000000", [item])
    second = await provider.create_lead_or_order("+79180000001", [item])
    assert first["ok"] and first["standalone"] is True
    assert first["number_1c"].startswith("ЛОКАЛ-")
    assert first["number_1c"] != second["number_1c"]


def test_storage_catalog_replace_and_preview(tmp_path):
    storage = Storage(tmp_path / "s.db")
    n = storage.replace_catalog([
        {"sku": "A1", "name": "Товар", "category": "категория",
         "price": 100.0, "stock": 5, "params_json": "{}"},
    ])
    assert n == 1 and storage.count_catalog() == 1
    storage.upsert_catalog_row({"sku": "A1", "name": "Товар v2", "category": "к",
                                "price": 120.0, "stock": 6, "params_json": "{}"})
    storage.upsert_catalog_row({"sku": "B2", "name": "Другой", "category": "к",
                                "price": 9.0, "stock": 1, "params_json": "{}"})
    assert storage.count_catalog() == 2
    rows = storage.list_catalog(search="v2")
    assert len(rows) == 1 and rows[0]["price"] == 120.0


def test_factory_picks_local_by_default(tmp_path):
    from app.integrations.catalog_provider import build_catalog_provider
    from app.integrations.one_c_client import OneCClient

    settings = AppSettings.load(tmp_path / "s.json")
    storage = Storage(tmp_path / "f.db")
    provider = build_catalog_provider(settings, storage, OneCClient(settings.one_c))
    assert provider.mode == "local"

    settings.catalog.mode = "one_c_odata"
    provider = build_catalog_provider(settings, storage, OneCClient(settings.one_c))
    assert provider.mode == "one_c_odata"
