"""Price-list parser for standalone mode: .xlsx/.xls (openpyxl) and .csv.

Headers are matched through a Russian synonym table, so customer files work
without a fixed template. Unknown columns are preserved as product parameters
and later participate in fuzzy matching.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Normalized header -> canonical field
_SYNONYMS: dict[str, str] = {
    "sku": ("артикул", "код", "кодтовара", "sku", "артикултовара", "кодноменклатуры"),
    "name": ("наименование", "товар", "номенклатура", "name", "название", "позиция"),
    "category": ("категория", "группа", "раздел", "вид", "тип", "класс"),
    "price": ("цена", "стоимость", "прайс", "price", "розничнаяцена", "ценаруб"),
    "stock": ("остаток", "колво", "количество", "наличие", "склад", "stock", "qty"),
}

CANONICAL_FIELDS = ("sku", "name", "category", "price", "stock")
SUPPORTED_SUFFIXES = (".xlsx", ".xls", ".csv")


class PriceListError(Exception):
    pass


@dataclass
class ParsedRow:
    sku: str
    name: str
    category: str = ""
    price: float = 0.0
    stock: int = 0
    params: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    skipped: int
    source: str


def _normalize_header(value: object) -> str:
    text = str(value or "").lower().strip()
    return re.sub(r"[^0-9a-zа-яё]+", "", text)


def _header_map(headers: list) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        norm = _normalize_header(raw)
        if not norm:
            continue
        for field_name, synonyms in _SYNONYMS.items():
            if norm in synonyms and field_name not in mapping:
                mapping[field_name] = idx
                break
    return mapping


def parse_number(value: object, default: float = 0.0) -> float:
    """'11 500,00 руб.' / '11500' / 1.250,30 -> float."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", " ").replace(" ", " ")
    # capture the whole numeric token including thousand/decimal separators
    match = re.search(r"-?\d[\d .\u00a0,]*\d|-?\d", text)
    if not match:
        return default
    token = match.group(0).replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in token and "." in token:
        # 1.250,30 (RU) or 1,250.30 (EN): the last separator is the decimal one
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    else:
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return default


def parse_price_list(path: str | Path) -> ParseResult:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise PriceListError(f"Формат {suffix or 'без расширения'} не поддерживается")
    if suffix == ".csv":
        rows, source = _parse_csv(path), "csv"
    elif suffix == ".xls":
        rows, source = _parse_xls(path), "xls"
    else:
        rows, source = _parse_xlsx(path), "xlsx"

    parsed, skipped = [], 0
    for raw in rows:
        item = _build_row(raw)
        if item is None:
            skipped += 1
            continue
        parsed.append(item)
    if not parsed:
        raise PriceListError("В файле не найдено ни одной пригодной строки товаров")
    log.info("parsed %s rows from %s (%s skipped)", len(parsed), path.name, skipped)
    return ParseResult(parsed, skipped, source)


def _build_row(raw: dict) -> ParsedRow | None:
    name = str(raw.get("name") or "").strip()
    sku = str(raw.get("sku") or "").strip()
    if not name and not sku:
        return None
    if not sku:
        sku = re.sub(r"\W+", "-", name.lower())[:40]
    params = {
        str(k).strip(): str(v).strip()
        for k, v in raw.items()
        if k not in CANONICAL_FIELDS and v not in (None, "") and str(v).strip()
    }
    return ParsedRow(
        sku=sku,
        name=name or sku,
        category=str(raw.get("category") or "").strip(),
        price=parse_number(raw.get("price")),
        stock=int(parse_number(raw.get("stock"))),
        params=params,
    )


def _parse_xlsx(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency guarded in requirements
        raise PriceListError("openpyxl не установлен") from exc

    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        headers = list(next(rows_iter))
    except StopIteration:
        return []
    mapping = _header_map(headers)
    if "name" not in mapping and "sku" not in mapping:
        raise PriceListError("Не найдены колонки «Наименование» или «Артикул»")
    return _zip_rows(headers, mapping, rows_iter)


def _parse_xls(path: Path) -> list[dict]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise PriceListError(
            "Legacy .xls требует пакет xlrd; сохраните файл в .xlsx"
        ) from exc
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    headers = sheet.row_values(0)
    mapping = _header_map(headers)
    if "name" not in mapping and "sku" not in mapping:
        raise PriceListError("Не найдены колонки «Наименование» или «Артикул»")
    rows_iter = (sheet.row_values(i) for i in range(1, sheet.nrows))
    return _zip_rows(headers, mapping, rows_iter)


def _parse_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise PriceListError("Не удалось определить кодировку CSV")

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
    except csv.Error:
        dialect = csv.excel  # fallback: comma
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        return []
    headers, data_rows = rows[0], rows[1:]
    mapping = _header_map(headers)
    if "name" not in mapping and "sku" not in mapping:
        raise PriceListError("Не найдены колонки «Наименование» или «Артикул»")
    return _zip_rows(headers, mapping, data_rows)


def _zip_rows(headers, mapping: dict[str, int], rows_iter) -> list[dict]:
    extra_cols = [i for i in range(len(headers)) if i not in mapping.values()]
    result: list[dict] = []
    for values in rows_iter:
        if values is None:
            continue
        row: dict = {}
        for field_name, idx in mapping.items():
            row[field_name] = values[idx] if idx < len(values) else ""
        for idx in extra_cols:
            if idx < len(values) and values[idx] not in (None, ""):
                row[str(headers[idx]).strip()] = values[idx]
        result.append(row)
    return result
