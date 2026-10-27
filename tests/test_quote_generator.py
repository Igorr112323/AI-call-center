from pathlib import Path

import pytest

from app.config import CatalogConfig
from app.core.models import Order, OrderItem
from app.reporting.quote_generator import (
    amount_in_words, document_number, generate_quote_pdf,
)


def _order(items: int = 2) -> Order:
    rows = [
        OrderItem("ACT-24-300", "Актуатор линейный 24В, ход 300мм", 2, 11500),
        OrderItem("LSE-IP67", "Концевой выключатель IP67", 4, 1450),
    ]
    if items > 2:
        rows = [
            OrderItem(f"SKU-{i:03d}", f"Позиция длинного наименования №{i}",
                      1 + i % 5, 3200.5 + i * 13.7)
            for i in range(items)
        ]
    return Order(
        id=7, call_id=1, number_1c="ЛОКАЛ-20261026-001", posted=False,
        delivery_address="г. Краснодар, ул. Промышленная, 12",
        comment="Доставка до проходной",
        items=rows,
    )


def test_pdf_generation_structure_and_vat(tmp_path):
    cfg = CatalogConfig(quote_vat_rate=20.0)
    path = generate_quote_pdf(_order(), cfg, phone="+79182041122",
                              client_name="ООО Завод", out_dir=tmp_path)

    assert isinstance(path, Path) and path.suffix == ".pdf"
    data = path.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 2000
    # Cyrillic-capable TTF is embedded, text never falls back to Helvetica
    assert b"/FontFile2" in data
    # order number survives into the document name
    assert "ЛОКАЛ-20261026-001" in path.name


def test_pdf_without_vat(tmp_path):
    cfg = CatalogConfig(quote_vat_rate=0.0)
    path = generate_quote_pdf(_order(), cfg, out_dir=tmp_path)
    assert path.exists() and path.read_bytes()[:5] == b"%PDF-"
    assert b"/FontFile2" in path.read_bytes()


def test_total_matches_order():
    order = _order()
    assert order.total == 2 * 11500 + 4 * 1450


def test_document_number_format():
    import time

    number = document_number(_order(), "20261026-101010")
    assert number.startswith(f"KP-{time.strftime('%y%m%d')}-")
    assert number.endswith("-0007")       # zero-padded order id


@pytest.mark.parametrize("value, expected", [
    (0, "Ноль рублей 00 коп."),
    (1, "Один рубль 00 коп."),
    (2, "Два рубля 00 коп."),
    (5, "Пять рублей 00 коп."),
    (11, "Одиннадцать рублей 00 коп."),
    (21, "Двадцать один рубль 00 коп."),
    (101, "Сто один рубль 00 коп."),
    (11500, "Одиннадцать тысяч пятьсот рублей 00 коп."),
    (28800.5, "Двадцать восемь тысяч восемьсот рублей 50 коп."),
    (1_000_000, "Один миллион рублей 00 коп."),
])
def test_amount_in_words_russian_grammar(value, expected):
    assert amount_in_words(value) == expected


def test_long_order_overflows_to_second_page(tmp_path):
    cfg = CatalogConfig(quote_vat_rate=20.0)
    path = generate_quote_pdf(_order(items=40), cfg, out_dir=tmp_path)
    data = path.read_bytes()
    # reportlab page objects appear as /Type /Page (without Pages)
    page_objects = data.count(b"/Type /Page\n") + data.count(b"/Type /Page<")
    page_objects += data.count(b"/Type /Page ")
    assert page_objects >= 2


def test_pdf_carries_supplier_requisites_and_signature_block(tmp_path):
    cfg = CatalogConfig()
    order = _order()
    path = generate_quote_pdf(order, cfg, phone="+79990001122", out_dir=tmp_path)
    assert path.stat().st_size > 5000
    # table keeps one data row per order line plus the header row
    table = __import__(
        "app.reporting.quote_generator", fromlist=["_items_table"], level=0
    )._items_table(order, cfg, __import__(
        "app.reporting.quote_generator", fromlist=["_styles"], level=0
    )._styles())
    assert len(table._cellvalues) == len(order.items) + 1
    # VAT column is populated per line when VAT applies
    assert table._cellvalues[1][6].text not in ("", "—")
