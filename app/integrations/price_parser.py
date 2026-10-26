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


_CURRENCY_TOKENS = ("руб.", "руб", "₽", "р.", "р", "$", "usd", "с ндс", "ндс")
_NUMBER_RE = re.compile(r"-?\d[\d\s.,]*\d|-?\d")


def sanitize_number_text(value: object) -> str:
    """Strip currency words and every kind of whitespace from a raw cell."""
    text = str(value).lower()
    for token in _CURRENCY_TOKENS:
        text = text.replace(token, "")
    # unify all unicode spaces (NBSP, narrow NBSP, thin space) to nothing later
    return "".join(text.split())


def parse_number(value: object, default: float = 0.0) -> float:
    """'11 500,00 руб.' / '11 500.00 ₽' / '1.250,30' / '#REF!' -> float."""
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    # formula errors and empty cells carry no number
    if not text.strip() or text.strip().startswith("#"):
        return default
    match = _NUMBER_RE.search(text)
    if not match:
        return default
    token = "".join(match.group(0).split())
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

    parsed_map: dict[str, ParsedRow] = {}
    skipped = duplicates = 0
    for raw in rows:
        item = _build_row(raw)
        if item is None:
            skipped += 1
            continue
        if item.sku in parsed_map:
            duplicates += 1  # last occurrence wins, mirrors ON CONFLICT upsert
        parsed_map[item.sku] = item
    parsed = list(parsed_map.values())
    if not parsed:
        raise PriceListError("В файле не найдено ни одной пригодной строки товаров")
    if duplicates:
        log.info("deduplicated %s repeated article rows", duplicates)
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
        price=max(0.0, parse_number(raw.get("price"))),
        stock=max(0, int(parse_number(raw.get("stock")))),
        params=params,
    )


def _is_blank_row(values) -> bool:
    return values is None or all(
        v is None or str(v).strip() == "" for v in values
    )


def _drop_leading_blank_rows(rows_iter):
    """Yield rows starting from the first non-empty one (title rows allowed)."""
    rows_iter = iter(rows_iter)
    for values in rows_iter:
        if not _is_blank_row(values):
            yield values
            yield from rows_iter
            return


def _parse_xlsx(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency guarded in requirements
        raise PriceListError("openpyxl не установлен") from exc

    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    sheet = workbook.active
    # Read-only worksheets expose no merged_cells API in current openpyxl,
    # so the ranges are read straight from the zipped sheet XML instead of
    # paying for a full (non-read-only) workbook load.
    merged = _read_merged_ranges(path)
    # fill runs first so its indices match the worksheet's file-level rows;
    # blank leading rows are dropped afterwards
    rows_iter = _drop_leading_blank_rows(
        _fill_merged_cells(sheet.iter_rows(values_only=True), merged))
    try:
        headers = list(next(rows_iter))
    except StopIteration:
        return []
    mapping = _header_map(headers)
    if "name" not in mapping and "sku" not in mapping:
        raise PriceListError("Не найдены колонки «Наименование» или «Артикул»")
    return _zip_rows(headers, mapping, rows_iter)


def _read_merged_ranges(path: Path) -> list:
    """Parse <mergeCell ref='C4:C5'/> entries from the xlsx worksheet XML."""
    import zipfile

    try:
        from openpyxl.utils.cell import range_boundaries
        with zipfile.ZipFile(path) as archive:
            sheet_names = [
                n for n in archive.namelist()
                if n.startswith("xl/worksheets/") and n.endswith(".xml")
            ]
            xml_blobs = [archive.read(n).decode("utf-8", "ignore") for n in sheet_names]
    except (zipfile.BadZipFile, KeyError, ImportError):
        return []

    ranges = []
    ref_re = re.compile(r'<mergeCell[^>]*ref="([A-Z]+\d+:[A-Z]+\d+)"')
    for blob in xml_blobs:
        for ref in ref_re.findall(blob):
            try:
                min_col, min_row, max_col, max_row = range_boundaries(ref)
                ranges.append(_MergedRange(min_row, max_row, min_col, max_col))
            except Exception:  # pragma: no cover - defensive against odd refs
                continue
    return ranges


class _MergedRange:
    def __init__(self, min_row: int, max_row: int, min_col: int, max_col: int):
        self.min_row, self.max_row = min_row, max_row
        self.min_col, self.max_col = min_col, max_col


def _fill_merged_cells(rows_iter, merged_ranges):
    """Propagate a merged range's top-left value into its covered cells.

    Customer price lists commonly merge category/name cells vertically;
    openpyxl returns None for covered cells, which would otherwise drop whole
    products. Only merged coordinates are filled, so genuinely empty price or
    stock cells stay empty instead of inheriting a neighbour's value.
    """
    covered: dict[tuple[int, int], tuple[int, int]] = {}
    for rng in merged_ranges:
        anchor = (rng.min_row - 1, rng.min_col - 1)
        for row in range(rng.min_row, rng.max_row + 1):
            for col in range(rng.min_col, rng.max_col + 1):
                coord = (row - 1, col - 1)
                if coord != anchor:
                    covered[coord] = anchor
    if not covered:
        yield from rows_iter
        return
    anchor_values: dict[tuple[int, int], object] = {}
    for r_idx, values in enumerate(rows_iter):
        values = list(values)
        for c_idx, value in enumerate(values):
            coord = (r_idx, c_idx)
            if coord in covered:
                values[c_idx] = anchor_values.get(covered[coord])
            elif value not in (None, ""):
                anchor_values[coord] = value
        yield values


def _parse_xls(path: Path) -> list[dict]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise PriceListError(
            "Legacy .xls требует пакет xlrd; сохраните файл в .xlsx"
        ) from exc
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    rows_iter = _drop_leading_blank_rows(
        (sheet.row_values(i) for i in range(sheet.nrows)))
    try:
        headers = list(next(rows_iter))
    except StopIteration:
        return []
    mapping = _header_map(headers)
    if "name" not in mapping and "sku" not in mapping:
        raise PriceListError("Не найдены колонки «Наименование» или «Артикул»")
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
    # Sniffer misjudges narrow files; trust an explicit dominant delimiter
    first_line = next((ln for ln in sample.splitlines() if ln.strip()), "")
    counts = {sep: first_line.count(sep) for sep in (";", "\t", "|", ",")}
    dominant = max(counts, key=counts.get)
    if counts[dominant] > 0 and (
            not dialect.delimiter or counts[dominant] > counts.get(dialect.delimiter, 0)):
        dialect = csv.excel()
        dialect.delimiter = dominant
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(_drop_leading_blank_rows(reader))
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
