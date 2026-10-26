"""Robustness tests for customer-grade 'dirty' price lists.

Covers delimiter/encoding chaos in CSV, merged cells and formula errors in
Excel, currency-suffixed text numbers, non-breaking spaces, duplicate
articles (upsert semantics) and a 10k-row batch insert performance budget.
"""
import csv
import io
import json
import time

import pytest

from app.config import CatalogConfig
from app.core.storage import Storage
from app.integrations.catalog_provider import LocalCatalogProvider
from app.integrations.price_parser import parse_number, parse_price_list

_HEADER = ["Артикул", "Наименование", "Категория", "Стоимость", "Кол-во"]
_ROWS = [
    ["ACT-24-300", "Актуатор 24В 300мм", "Актуаторы", "11 500,00 руб. с НДС", "14"],
    ["ACT-12-150", "Актуатор 12В 150мм", "Актуаторы", "4 200,00 руб.", "60"],
    ["LSE-IP67", "Концевой выключатель IP67", "Датчики", "#REF!", "9"],
]


def _write_csv(path, delimiter: str, encoding: str) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow(_HEADER)
    writer.writerows(_ROWS)
    path.write_bytes(buffer.getvalue().encode(encoding))


@pytest.mark.parametrize("delimiter", [";", "\t", "|", ","])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1251"])
def test_csv_every_delimiter_and_encoding(tmp_path, delimiter, encoding):
    path = tmp_path / "price.csv"
    _write_csv(path, delimiter, encoding)
    result = parse_price_list(path)
    assert len(result.rows) == 3
    first = next(r for r in result.rows if r.sku == "ACT-24-300")
    assert first.price == 11500.0
    assert first.stock == 14
    assert first.category == "Актуаторы"
    # formula error cell degrades to zero, row is still imported
    broken = next(r for r in result.rows if r.sku == "LSE-IP67")
    assert broken.price == 0.0


def test_csv_with_blank_leading_lines(tmp_path):
    path = tmp_path / "blank.csv"
    content = "\n\n   \n" + "\n".join([
        ";".join(_HEADER),
        ";".join(_ROWS[0]),
    ])
    path.write_text(content, encoding="utf-8")
    result = parse_price_list(path)
    assert len(result.rows) == 1
    assert result.rows[0].sku == "ACT-24-300"


def test_xlsx_merged_cells_and_dirty_numbers(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "merged.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append([])                       # blank leading row
    ws.append([])
    ws.append(_HEADER)
    ws.append(["ACT-24-300", "Актуатор 24В 300мм", None, "11 500.00 руб с НДС", "14"])
    ws.append(["ACT-24-500", "Актуатор 24В 500мм", None, "18 000,00", "6"])
    # merge the category column across both product rows + a vertical name merge
    ws.merge_cells("C4:C5")
    ws["C4"] = "Актуаторы"
    ws.append(["SRV-750-B", "Сервопривод 750Вт", "Приводы", "42 000 ₽", "2"])
    wb.save(path)

    result = parse_price_list(path)
    assert len(result.rows) == 3
    by_sku = {r.sku: r for r in result.rows}
    assert by_sku["ACT-24-300"].category == "Актуаторы"
    # merged category propagated into the covered row
    assert by_sku["ACT-24-500"].category == "Актуаторы"
    assert by_sku["ACT-24-300"].price == 11500.0
    assert by_sku["ACT-24-500"].price == 18000.0
    assert by_sku["SRV-750-B"].stock == 2


@pytest.mark.parametrize("raw, expected", [
    ("11 500.00 руб с НДС", 11500.0),
    ("7\xa0500", 7500.0),              # non-breaking space
    ("7\u202f200,50", 7200.5),         # narrow non-breaking space + comma
    ("5\xa0000,00 ₽", 5000.0),
    ("$1 250.30", 1250.30),
    ("#REF!", 0.0),
    ("#DIV/0!", 0.0),
    ("", 0.0),
    (None, 0.0),
    (42, 42.0),
    ("-100", -100.0),                  # parser keeps sign; row sanitizer clamps
])
def test_parse_number_sanitizer(raw, expected):
    assert parse_number(raw) == expected


def test_duplicate_articles_upsert_not_crash(tmp_path):
    storage = Storage(tmp_path / "dup.db")
    provider = LocalCatalogProvider(storage, CatalogConfig())
    path = tmp_path / "dup.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(_HEADER)
        writer.writerow(["DUP-1", "Товар v1", "Кат", "1000", "1"])
        writer.writerow(["DUP-1", "Товар v2", "Кат", "1200", "2"])
        writer.writerow(["DUP-2", "Товар другой", "Кат", "500", "3"])

    count = provider.import_file(path)
    assert count == 2                   # one row per sku, last price wins
    rows = {r["sku"]: r for r in provider.preview(limit=10)}
    assert rows["DUP-1"]["name"] == "Товар v2"
    assert rows["DUP-1"]["price"] == 1200.0
    storage.close()


def test_batch_insert_10k_rows_under_budget(tmp_path):
    storage = Storage(tmp_path / "big.db")
    rows = [
        {
            "sku": f"SKU-{i:05d}",
            "name": f"Позиция {i}",
            "category": "Каталог",
            "price": 1000.0 + i,
            "stock": i % 50,
            "params_json": json.dumps({"voltage": 24}),
        }
        for i in range(10_000)
    ]
    started = time.perf_counter()
    count = storage.replace_catalog(rows)
    elapsed = time.perf_counter() - started
    assert count == 10_000
    assert storage.count_catalog() == 10_000
    assert elapsed < 1.5, f"batch insert took {elapsed:.3f}s"
    storage.close()


def test_full_import_10k_csv_round_trip(tmp_path):
    storage = Storage(tmp_path / "big.db")
    provider = LocalCatalogProvider(storage, CatalogConfig())
    path = tmp_path / "big.csv"
    with path.open("w", encoding="cp1251", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(_HEADER)
        for i in range(10_000):
            writer.writerow([
                f"SKU-{i:05d}", f"Актуатор {i}", "Актуаторы",
                f"{4500 + i} руб.", str(i % 40),
            ])
    count = provider.import_file(path)
    assert count == 10_000
    sample = next(r for r in provider.preview(limit=10_000) if r["sku"] == "SKU-09999")
    assert sample["price"] == 14499.0
    storage.close()
