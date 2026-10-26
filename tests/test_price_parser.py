import csv

import pytest
from openpyxl import Workbook

from app.integrations.price_parser import (
    PriceListError, parse_number, parse_price_list,
)


def _write_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_parse_xlsx_with_synonym_headers(tmp_path):
    path = tmp_path / "price.xlsx"
    _write_xlsx(path, [
        ["Код", "Товар", "Группа", "Прайс", "Кол-во", "Напряжение"],
        ["ACT-24-300", "Актуатор 24В 300мм", "актуатор", 11500, 14, 24],
        ["LSE-IP67", "Концевой выключатель", "выключатель", "1 450,50", "120", ""],
    ])

    result = parse_price_list(path)
    assert result.source == "xlsx" and len(result.rows) == 2
    first = result.rows[0]
    assert first.sku == "ACT-24-300"
    assert first.category == "актуатор"
    assert first.price == 11500 and first.stock == 14
    assert first.params == {"Напряжение": "24"}

    second = result.rows[1]
    assert second.price == 1450.5 and second.stock == 120


def test_parse_csv_semicolon_cp1251(tmp_path):
    path = tmp_path / "price.csv"
    with path.open("w", encoding="cp1251", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Артикул", "Наименование", "Стоимость", "Остаток"])
        writer.writerow(["MTR-15", "Двигатель 15кВт", "92 400.00", "3"])

    result = parse_price_list(path)
    assert result.source == "csv"
    row = result.rows[0]
    assert row.sku == "MTR-15" and row.price == 92400.0 and row.stock == 3


def test_missing_name_generates_sku_and_blank_rows_skipped(tmp_path):
    path = tmp_path / "loose.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Артикул", "Наименование", "Цена", "Остаток"])
        writer.writerow(["", "", "", ""])
        writer.writerow(["X1", "Муфта соединительная", "300", "5"])
    result = parse_price_list(path)
    assert len(result.rows) == 1 and result.skipped == 1
    assert result.rows[0].name == "Муфта соединительная"


def test_unsupported_format_and_bad_headers(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(PriceListError):
        parse_price_list(bad)

    empty = tmp_path / "empty.xlsx"
    _write_xlsx(empty, [["Документ", "Дата"], ["КП", "01.01"]])
    with pytest.raises(PriceListError):
        parse_price_list(empty)


def test_parse_number_variants():
    assert parse_number("11 500,00 руб.") == 11500.0
    assert parse_number("1.250,30") == 1250.30
    assert parse_number(42) == 42.0
    assert parse_number("нет данных", default=7.0) == 7.0
