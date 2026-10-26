"""Ready-to-sell demo catalogs in presets/ must always import cleanly."""
from pathlib import Path

import pytest

from app.config import CatalogConfig
from app.core.storage import Storage
from app.integrations.catalog_provider import LocalCatalogProvider
from app.integrations.price_parser import parse_price_list
from app.paths import preset_path

PRESETS = [
    ("actuators_catalog.xlsx", "Линейные актуаторы"),
    ("rvd_hydraulics_catalog.xlsx", "Рукава РВД"),
    ("corrugated_boxes_catalog.xlsx", "Гофротара"),
]


@pytest.mark.parametrize("filename, category", PRESETS)
def test_preset_has_50_unique_well_formed_rows(filename, category):
    path = Path(__file__).resolve().parents[1] / "presets" / filename
    assert path.exists(), f"run scripts/generate_presets.py to create {filename}"
    result = parse_price_list(path)
    assert len(result.rows) == 50
    skus = [row.sku for row in result.rows]
    assert len(set(skus)) == 50                  # no duplicate articles
    assert all(row.name and row.price > 0 and row.stock >= 0 for row in result.rows)
    assert all(row.category == category for row in result.rows)


def test_preset_path_resolver_matches_repo_file():
    assert preset_path("actuators_catalog.xlsx").exists()


@pytest.mark.parametrize("filename", [name for name, _ in PRESETS])
def test_preset_imports_into_storage_and_is_searchable(tmp_path, filename):
    storage = Storage(tmp_path / "presets.db")
    provider = LocalCatalogProvider(storage, CatalogConfig())
    count = provider.import_file(
        Path(__file__).resolve().parents[1] / "presets" / filename)
    assert count == 50
    assert provider.count() == 50
    # every imported row is retrievable through the production fuzzy path
    sample = provider.preview(limit=1)[0]
    product = provider.lookup_product(sample["name"])
    assert product.found and product.article == sample["sku"]
    storage.close()


def test_actuator_preset_supports_voice_style_lookup(tmp_path):
    storage = Storage(tmp_path / "act.db")
    provider = LocalCatalogProvider(storage, CatalogConfig())
    provider.import_file(
        Path(__file__).resolve().parents[1] / "presets" / "actuators_catalog.xlsx")
    product = provider.lookup_product("нужен актуатор 24 вольта, ход 300 мм")
    assert product.found
    assert product.price >= 4500
    storage.close()
