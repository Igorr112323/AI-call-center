"""Commercial offer / invoice PDF generation (reportlab).

Bank-grade A4 document: embedded Cyrillic TTF (DejaVu, free license),
supplier/customer requisites, per-line VAT, amount in words, signature block
and automatic page overflow with numbered pages.
"""
from __future__ import annotations

import time
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from ..config import CatalogConfig
from ..core.models import Order
from ..paths import resource_path

SLATE = colors.HexColor("#16191F")
MUTED = colors.HexColor("#7A8394")
BORDER = colors.HexColor("#D6DAE1")
TABLE_BORDER = colors.HexColor("#C7CDD6")
ACCENT = colors.HexColor("#10B981")
ROW_ALT = colors.HexColor("#F8FAFC")
TOTAL_BG = colors.HexColor("#EEF2F6")
HEADER_BG = colors.HexColor("#16191F")
HEADER_FG = colors.HexColor("#E6E8EC")

_FONT = "DejaVu"
_FONT_BOLD = "DejaVu-Bold"
_fonts_ready = False


def _ensure_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    pdfmetrics.registerFont(TTFont(_FONT, resource_path("fonts/DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, resource_path("fonts/DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        _FONT, normal=_FONT, bold=_FONT_BOLD, italic=_FONT, boldItalic=_FONT_BOLD)
    _fonts_ready = True


# ------------------------------------------------------------- amount in words
_ONES = (
    "", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
    "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
)
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
         "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот",
             "семьсот", "восемьсот", "девятьсот")


def _triplet(number: int) -> list[str]:
    words: list[str] = []
    if number >= 100:
        words.append(_HUNDREDS[number // 100])
        number %= 100
    if number >= 20:
        words.append(_TENS[number // 10])
        number %= 10
    if number:
        words.append(_ONES[number])
    return words


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    tail = n % 10
    if tail == 1:
        return forms[0]
    if 2 <= tail <= 4:
        return forms[1]
    return forms[2]


_SCALES = (
    (1_000_000, ("миллион", "миллиона", "миллионов"), False),
    (1_000, ("тысяча", "тысячи", "тысяч"), True),
)


def amount_in_words(value: float) -> str:
    """28800.50 -> 'Двадцать восемь тысяч восемьсот рублей 50 коп.'"""
    rubles = int(value)
    kopecks = round((value - rubles) * 100)
    if rubles == 0:
        body = "Ноль"
    else:
        parts: list[str] = []
        remainder = rubles
        for scale, forms, feminine in _SCALES:
            count, remainder = divmod(remainder, scale)
            if not count:
                continue
            words = _triplet(count)
            if feminine:
                words = ["одна" if w == "один"
                         else "две" if w == "два" else w for w in words]
            parts.append(" ".join(words))
            parts.append(_plural(count, forms))
        if remainder:
            parts.append(" ".join(_triplet(remainder)))
        body = " ".join(parts).capitalize()
    return (f"{body} {_plural(rubles, ('рубль', 'рубля', 'рублей'))} "
            f"{kopecks:02d} коп.")


# ------------------------------------------------------------------- rendering
def _styles() -> dict:
    base = getSampleStyleSheet()["Normal"]
    base.fontName = _FONT
    return {
        "company": ParagraphStyle("company", parent=base, fontName=_FONT_BOLD,
                                  fontSize=13, textColor=SLATE, leading=16),
        "requisites": ParagraphStyle("requisites", parent=base, fontSize=8,
                                     textColor=SLATE, leading=11),
        "title": ParagraphStyle("title", parent=base, fontName=_FONT_BOLD,
                                fontSize=12, textColor=SLATE, leading=15, spaceBefore=8),
        "meta": ParagraphStyle("meta", parent=base, fontSize=9, textColor=MUTED, leading=13),
        "meta_bold": ParagraphStyle("meta_bold", parent=base, fontName=_FONT_BOLD,
                                    fontSize=9, textColor=SLATE, leading=13),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, textColor=SLATE, leading=11),
        "cell_right": ParagraphStyle("cell_right", parent=base, fontSize=8.5,
                                     textColor=SLATE, alignment=TA_RIGHT, leading=11),
        "head": ParagraphStyle("head", parent=base, fontName=_FONT_BOLD,
                               fontSize=8, textColor=HEADER_FG, leading=10),
        "head_right": ParagraphStyle("head_right", parent=base, fontName=_FONT_BOLD,
                                     fontSize=8, textColor=HEADER_FG,
                                     alignment=TA_RIGHT, leading=10),
        "total": ParagraphStyle("total", parent=base, fontName=_FONT_BOLD,
                                fontSize=11, textColor=SLATE, alignment=TA_RIGHT, leading=14),
        "words": ParagraphStyle("words", parent=base, fontSize=9, textColor=SLATE, leading=13),
        "note": ParagraphStyle("note", parent=base, fontSize=7.5, textColor=MUTED, leading=10),
        "sign": ParagraphStyle("sign", parent=base, fontSize=8.5, textColor=SLATE, leading=12),
    }


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


class NumberedCanvas(pdf_canvas.Canvas):
    """Draws 'Стр. N из M' once the total page count is known."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        self.setFont(_FONT, 7.5)
        self.setFillColor(MUTED)
        self.drawString(18 * mm, 9 * mm,
                        "Документ сформирован AI-агентом продаж · подпись не требуется для электронной копии")
        self.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Стр. {self._pageNumber} из {total}")


def document_number(order: Order, stamp: str) -> str:
    seq = f"{order.id or 0:04d}"
    return f"KP-{time.strftime('%y%m%d')}-{seq}"


def generate_quote_pdf(order: Order, catalog_cfg: CatalogConfig, *,
                       phone: str = "", client_name: str = "",
                       out_dir: str | Path | None = None) -> Path:
    _ensure_fonts()
    out_dir = Path(out_dir or Path.cwd())
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    doc_id = document_number(order, stamp)
    safe = (order.number_1c or doc_id).replace(" ", "_").replace("/", "-")
    path = out_dir / f"KP_{safe}_{stamp}.pdf"

    styles = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"КП {doc_id}", author=catalog_cfg.quote_company_name,
    )
    story = _build_story(order, catalog_cfg, phone, client_name, doc_id, styles)
    doc.build(story, canvasmaker=NumberedCanvas)
    return path


def _build_story(order: Order, cfg: CatalogConfig, phone: str,
                 client_name: str, doc_id: str, styles: dict) -> list:
    story: list = []
    story.extend(_header_block(order, cfg, phone, client_name, doc_id, styles))
    story.append(Spacer(1, 5 * mm))
    story.append(_items_table(order, cfg, styles))
    story.append(Spacer(1, 4 * mm))
    story.append(_total_block(order, cfg, styles))
    story.append(Spacer(1, 8 * mm))
    story.append(_signature_block(styles))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "Счёт действителен в течение трёх банковских дней. Цены включают НДС, "
        "если применимо. Оплата означает согласие с условиями поставки.",
        styles["note"]))
    return story


def _header_block(order: Order, cfg: CatalogConfig, phone: str,
                  client_name: str, doc_id: str, styles: dict) -> list:
    requisites = (
        f"<b>{cfg.quote_company_name}</b><br/>"
        f"Юр. адрес: {cfg.quote_legal_address}<br/>"
        f"ИНН {cfg.quote_inn} · КПП {cfg.quote_kpp} · ОГРН {cfg.quote_ogrn}<br/>"
        f"{cfg.quote_bank}<br/>"
        f"р/с {cfg.quote_bank_account} · к/с {cfg.quote_corr_account} · БИК {cfg.quote_bik}"
    )
    status = "Черновик" if not order.posted else "Проведён"
    meta = (
        f"<b>Документ:</b> {doc_id}<br/>"
        f"Статус: {status}<br/>"
        f"Дата и время: {time.strftime('%d.%m.%Y %H:%M')}<br/>"
        f"Заказ клиента: {order.number_1c or '—'}"
    )
    head = Table([[Paragraph(requisites, styles["requisites"]),
                   Paragraph(meta, styles["meta"])]],
                 colWidths=[105 * mm, 73 * mm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    customer = (
        f"<b>Покупатель:</b> {client_name or 'Розничный клиент'}<br/>"
        f"Телефон: {phone or '—'}<br/>"
        f"Адрес доставки: {order.delivery_address or '—'}"
        + (f"<br/>Комментарий: {order.comment}" if order.comment else "")
    )
    return [
        head,
        Spacer(1, 3 * mm),
        _accent_rule(),
        Spacer(1, 3 * mm),
        Paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ / СЧЁТ НА ОПЛАТУ", styles["title"]),
        Spacer(1, 2 * mm),
        Paragraph(customer, styles["requisites"]),
    ]


def _items_table(order: Order, cfg: CatalogConfig, styles: dict) -> Table:
    vat_rate = cfg.quote_vat_rate
    header = [
        Paragraph("№", styles["head"]),
        Paragraph("Артикул", styles["head"]),
        Paragraph("Наименование", styles["head"]),
        Paragraph("Кол-во", styles["head_right"]),
        Paragraph("Ед.", styles["head_right"]),
        Paragraph("Цена без НДС, ₽", styles["head_right"]),
        Paragraph(f"НДС {vat_rate:g}%, ₽", styles["head_right"]),
        Paragraph("Сумма, ₽", styles["head_right"]),
    ]
    data = [header]
    for idx, item in enumerate(order.items, start=1):
        gross = item.price * item.quantity
        if vat_rate > 0:
            net = gross / (1 + vat_rate / 100)
            vat = gross - net
            net_price = item.price / (1 + vat_rate / 100)
            net_text, vat_text = money(net_price), money(vat)
        else:
            net_text, vat_text = money(item.price), "—"
        data.append([
            Paragraph(str(idx), styles["cell"]),
            Paragraph(item.article, styles["cell"]),
            Paragraph(item.name, styles["cell"]),
            Paragraph(f"{item.quantity:g}", styles["cell_right"]),
            Paragraph(item.unit, styles["cell_right"]),
            Paragraph(net_text, styles["cell_right"]),
            Paragraph(vat_text, styles["cell_right"]),
            Paragraph(money(gross), styles["cell_right"]),
        ])

    table = Table(
        data,
        colWidths=[8 * mm, 24 * mm, 52 * mm, 15 * mm, 10 * mm,
                   24 * mm, 20 * mm, 25 * mm],
        repeatRows=1,
    )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, SLATE),
        ("GRID", (0, 1), (-1, -1), 0.35, TABLE_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    table.setStyle(TableStyle(style))
    return table


def _total_block(order: Order, cfg: CatalogConfig, styles: dict) -> Table:
    total = order.total
    if cfg.quote_vat_rate > 0:
        vat = round(total * cfg.quote_vat_rate / (100 + cfg.quote_vat_rate), 2)
        net = total - vat
        vat_line = f"В том числе НДС {cfg.quote_vat_rate:g}%: {money(vat)} ₽"
    else:
        net, vat_line = total, "НДС не облагается (УСН)."
    cells = [
        [Paragraph(f"Сумма без НДС: {money(net)} ₽", styles["meta"])],
        [Paragraph(vat_line, styles["meta"])],
        [Paragraph(f"ИТОГО С НДС: {money(total)} ₽", styles["total"])],
        [Paragraph(amount_in_words(total), styles["words"])],
    ]
    block = Table(cells, colWidths=[178 * mm])
    block.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TOTAL_BG),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return block


def _signature_block(styles: dict) -> Table:
    row = Table([[
        Paragraph("Руководитель организации:", styles["sign"]),
        Paragraph("М.П.", styles["sign"]),
    ]], colWidths=[110 * mm, 40 * mm])
    row.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 0.6, SLATE),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return row


def _accent_rule() -> Table:
    rule = Table([[""]], colWidths=[178 * mm], rowHeights=[1.4])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    return rule
