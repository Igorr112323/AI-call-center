"""Commercial offer / invoice PDF generation (reportlab).

Compact, typographic document aligned with the product's restrained visual
language: dark slate headings, single emerald accent rule, mono-aligned money.
"""
from __future__ import annotations

import time
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from ..config import CatalogConfig
from ..core.models import Order

SLATE = colors.HexColor("#16191F")
MUTED = colors.HexColor("#7A8394")
BORDER = colors.HexColor("#222730")
ACCENT = colors.HexColor("#10B981")
ROW_ALT = colors.HexColor("#F6F7F9")


def _styles() -> dict:
    base = getSampleStyleSheet()["Normal"]
    return {
        "company": ParagraphStyle("company", parent=base, fontName="Helvetica-Bold",
                                  fontSize=14, textColor=SLATE, leading=18),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold",
                                fontSize=11, textColor=SLATE, leading=14, spaceBefore=10),
        "meta": ParagraphStyle("meta", parent=base, fontSize=9, textColor=MUTED, leading=13),
        "cell": ParagraphStyle("cell", parent=base, fontSize=9, textColor=SLATE, leading=12),
        "cell_right": ParagraphStyle("cell_right", parent=base, fontSize=9,
                                     textColor=SLATE, alignment=TA_RIGHT, leading=12),
        "head": ParagraphStyle("head", parent=base, fontName="Helvetica-Bold",
                               fontSize=8, textColor=MUTED, leading=11),
        "head_right": ParagraphStyle("head_right", parent=base, fontName="Helvetica-Bold",
                                     fontSize=8, textColor=MUTED, alignment=TA_RIGHT, leading=11),
        "total": ParagraphStyle("total", parent=base, fontName="Helvetica-Bold",
                                fontSize=11, textColor=SLATE, alignment=TA_RIGHT),
        "note": ParagraphStyle("note", parent=base, fontSize=8, textColor=MUTED, leading=11),
    }


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def generate_quote_pdf(order: Order, catalog_cfg: CatalogConfig, *,
                       phone: str = "", client_name: str = "",
                       out_dir: str | Path | None = None) -> Path:
    out_dir = Path(out_dir or Path.cwd())
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    number = (order.number_1c or f"LOCAL-{order.id or stamp}").replace(" ", "_")
    path = out_dir / f"KP_{number}_{stamp}.pdf"

    styles = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"КП {number}",
    )
    story = _build_story(order, catalog_cfg, phone, client_name, number, styles)
    doc.build(story)
    return path


def _build_story(order: Order, cfg: CatalogConfig, phone: str,
                 client_name: str, number: str, styles: dict) -> list:
    story: list = [
        Paragraph(cfg.quote_company_name, styles["company"]),
        Spacer(1, 2 * mm),
        _accent_rule(),
        Spacer(1, 4 * mm),
        Paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ / СЧЁТ НА ОПЛАТУ", styles["title"]),
        Paragraph(
            f"Документ: {number} &nbsp;&nbsp; Дата: {time.strftime('%d.%m.%Y')}",
            styles["meta"],
        ),
        Paragraph(
            f"Клиент: {client_name or '—'} &nbsp;&nbsp; Телефон: {phone or '—'}",
            styles["meta"],
        ),
        Spacer(1, 6 * mm),
    ]

    header = [
        Paragraph("Артикул", styles["head"]),
        Paragraph("Наименование", styles["head"]),
        Paragraph("Кол-во", styles["head_right"]),
        Paragraph("Цена, ₽", styles["head_right"]),
        Paragraph("Сумма, ₽", styles["head_right"]),
    ]
    data = [header]
    for item in order.items:
        data.append([
            Paragraph(item.article, styles["cell"]),
            Paragraph(item.name, styles["cell"]),
            Paragraph(f"{item.quantity:g} {item.unit}", styles["cell_right"]),
            Paragraph(money(item.price), styles["cell_right"]),
            Paragraph(money(item.sum), styles["cell_right"]),
        ])

    table = Table(
        data,
        colWidths=[32 * mm, 72 * mm, 20 * mm, 27 * mm, 27 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, BORDER),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))

    total = order.total
    if cfg.quote_vat_rate > 0:
        vat = round(total * cfg.quote_vat_rate / (100 + cfg.quote_vat_rate), 2)
        story.append(Paragraph(f"В том числе НДС {cfg.quote_vat_rate:g}%: {money(vat)} ₽",
                               styles["meta"]))
    else:
        story.append(Paragraph("НДС не облагается (УСН).", styles["meta"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(f"ИТОГО К ОПЛАТЕ: {money(total)} ₽", styles["total"]))
    story.append(Spacer(1, 8 * mm))
    story.append(_accent_rule())
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "Предложение сформировано голосовым AI-агентом. Цены действительны на дату документа.",
        styles["note"],
    ))
    return story


def _accent_rule() -> Table:
    rule = Table([[""]], colWidths=[178 * mm], rowHeights=[1.4])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    return rule
