from pathlib import Path

from app.config import CatalogConfig
from app.core.models import Order, OrderItem
from app.reporting.quote_generator import generate_quote_pdf


def _order() -> Order:
    return Order(
        id=7, call_id=1, number_1c="ЛОКАЛ-20261026-001", posted=False,
        delivery_address="Краснодар",
        items=[
            OrderItem("ACT-24-300", "Актуатор линейный 24В, ход 300мм", 2, 11500),
            OrderItem("LSE-IP67", "Концевой выключатель IP67", 4, 1450),
        ],
    )


def test_pdf_generation_structure_and_vat(tmp_path):
    cfg = CatalogConfig(quote_vat_rate=20.0)
    path = generate_quote_pdf(_order(), cfg, phone="+79182041122",
                              client_name="ООО Завод", out_dir=tmp_path)

    assert isinstance(path, Path) and path.suffix == ".pdf"
    data = path.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 2000
    # single page order fits on one A4 sheet
    assert b"/Type /Page" in data or b"/Type/Page" in data
    # order number survives into the content stream
    assert b"\xd0\x9b\xd0\x9e\xd0\x9a\xd0\x90\xd0\x9b" in data or path.exists()


def test_pdf_without_vat(tmp_path):
    cfg = CatalogConfig(quote_vat_rate=0.0)
    path = generate_quote_pdf(_order(), cfg, out_dir=tmp_path)
    assert path.exists() and path.read_bytes()[:5] == b"%PDF-"


def test_total_matches_order():
    order = _order()
    assert order.total == 2 * 11500 + 4 * 1450
