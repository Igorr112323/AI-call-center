"""Catalog providers — one interface over a local price list or a live 1C base.

``LocalCatalogProvider`` is the default standalone source: the imported
Excel/CSV price list lives in SQLite, matching is fully offline with a
sub-5 ms fuzzy scorer. ``OneCCatalogProvider`` adapts the existing 1C
connector (HTTP-service or OData) to the same contract, so the dialog brain
never knows where the data comes from.
"""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import CatalogConfig
from ..core.models import OrderItem, ProductStock
from ..core.storage import Storage
from .price_parser import ParsedRow, parse_price_list

log = logging.getLogger(__name__)


class CatalogError(Exception):
    pass


# --------------------------------------------------------------------- parsing
_VOLTAGE_RE = re.compile(r"(\d{1,3})\s*(?:вольт\w*|в\.?|v\b)", re.IGNORECASE)
_STROKE_RE = re.compile(r"(\d{2,4})\s*мм", re.IGNORECASE)
_FORCE_RE = re.compile(r"(\d{2,5})\s*(?:ньютон\w*|н\.?|n\b|кг)", re.IGNORECASE)
_QUANTITY_RE = re.compile(r"(\d{1,4})\s*(?:шт\w*|единиц\w*|штук\w*)", re.IGNORECASE)
_RU_NUMERALS = {
    "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10,
}
_NUMBER_TOKEN_RE = re.compile(r"\d+")

_PARAM_ALIASES = {
    "voltage": ("напряжение", "вольт", "в", "v"),
    "stroke_mm": ("ход", "мм", "mm", "шток"),
    "force_n": ("усилие", "ньютон", "н", "n"),
}


def extract_params(query: str) -> dict:
    """Pull engineering parameters out of free-form Russian speech."""
    params: dict = {}
    if match := _VOLTAGE_RE.search(query):
        params["voltage"] = int(match.group(1))
    if match := _STROKE_RE.search(query):
        params["stroke_mm"] = int(match.group(1))
    if match := _FORCE_RE.search(query):
        params["force_n"] = int(match.group(1))
    if match := _QUANTITY_RE.search(query):
        params["quantity"] = int(match.group(1))
    else:
        for word in re.split(r"[^\w]+", query.lower()):
            if word in _RU_NUMERALS:
                params["quantity"] = _RU_NUMERALS[word]
                break
    return params


def _text_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if len(t) > 2]


class BaseCatalogProvider(ABC):
    mode = "base"

    @abstractmethod
    async def get_product_price_and_stock(self, sku_or_name: str,
                                          params: dict | None = None) -> ProductStock: ...

    @abstractmethod
    async def create_lead_or_order(self, phone: str, items: list[OrderItem],
                                   comment: str = "", address: str = "") -> dict: ...

    async def ping(self) -> dict:
        return {"ok": True, "mode": self.mode}

    async def aclose(self) -> None: ...


# ------------------------------------------------------------------ standalone
class LocalCatalogProvider(BaseCatalogProvider):
    mode = "local"

    def __init__(self, storage: Storage, config: CatalogConfig):
        self.storage = storage
        self.config = config
        self._cache: list[dict] | None = None
        self._local_seq = 0

    # ------------------------------------------------------------ imports
    def import_file(self, path: str | Path) -> int:
        result = parse_price_list(path)
        rows = [
            {
                "sku": row.sku,
                "name": row.name,
                "category": row.category,
                "price": row.price,
                "stock": row.stock,
                "params_json": json.dumps(row.params, ensure_ascii=False),
            }
            for row in result.rows
        ]
        count = self.storage.replace_catalog(rows)
        self.config.last_import_path = str(path)
        self._cache = None
        log.info("local catalog imported: %s items, %s skipped", count, result.skipped)
        return count

    def count(self) -> int:
        return self.storage.count_catalog()

    def preview(self, limit: int = 50, search: str = "") -> list[dict]:
        return [dict(row) for row in self.storage.list_catalog(limit=limit, search=search)]

    # ------------------------------------------------------------ matching
    def _rows(self) -> list[dict]:
        if self._cache is None:
            self._cache = [dict(row) for row in self.storage.all_catalog()]
        return self._cache

    def lookup_product(self, query: str, explicit_params: dict | None = None) -> ProductStock:
        """Synchronous fuzzy lookup; the async wrapper delegates here."""
        t0 = time.perf_counter()
        explicit_params = explicit_params or {}
        params = extract_params(query)
        params.update({k: v for k, v in explicit_params.items() if v is not None})

        query_tokens = _text_tokens(query)
        best: tuple[int, dict] | None = None
        for row in self._rows():
            score = self._score_row(row, query, query_tokens, params)
            if best is None or score > best[0]:
                best = (score, row)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.debug("fuzzy lookup %r -> score=%s in %.3f ms", query,
                  best[0] if best else None, elapsed_ms)
        if not best or best[0] < self.config.fuzzy_min_score:
            return ProductStock(found=False, raw={"query": query, "best_score":
                                                   best[0] if best else 0})
        row = best[1]
        return ProductStock(
            found=True,
            article=row["sku"],
            name=row["name"],
            price=float(row["price"]),
            stock=float(row["stock"]),
            warehouse="Локальный прайс-лист",
            raw={"score": best[0], "params": json.loads(row.get("params_json") or "{}")},
        )

    async def get_product_price_and_stock(self, sku_or_name: str,
                                          params: dict | None = None) -> ProductStock:
        return self.lookup_product(sku_or_name, params)

    def _score_row(self, row: dict, query: str, query_tokens: list[str],
                   params: dict) -> int:
        score = 0
        haystack = f"{row['sku']} {row['name']} {row['category']}".lower()
        product_params = json.loads(row.get("params_json") or "{}")

        # exact SKU / strong substring signals
        if row["sku"] and row["sku"].lower() in query.lower():
            score += 60
        if row["category"] and row["category"].lower() in query.lower():
            score += 25

        for token in query_tokens:
            if token in haystack:
                score += 10

        # engineering parameters are the decisive signal in B2B speech:
        # an explicit mismatch (24V requested vs 380V row) must disqualify
        for param_name, value in params.items():
            if param_name == "quantity":
                continue
            if self._param_matches(param_name, value, row, product_params):
                score += 35
            else:
                score -= 60

        # numbers mentioned in the query must appear in name/params/SKU
        for number in _NUMBER_TOKEN_RE.findall(query):
            if number in str(row["sku"]) or number in row["name"] \
                    or any(number in str(v) for v in product_params.values()):
                score += 8
        return score

    @staticmethod
    def _param_matches(param_name: str, value, row: dict, product_params: dict) -> bool:
        if param_name in product_params and str(value) == str(product_params[param_name]):
            return True
        blob = f"{row['name']} {row['sku']} {' '.join(map(str, product_params.values()))}".lower()
        aliases = _PARAM_ALIASES.get(param_name, (param_name,))
        token = str(value)
        # "24" in "24в" style names, or "300" next to "мм"
        if token in blob:
            if param_name != "voltage" or any(alias in blob for alias in aliases):
                return True
        return any(
            re.search(rf"{re.escape(token)}\s*{re.escape(alias[:1])}", blob)
            for alias in aliases if len(alias) >= 1
        )

    # ------------------------------------------------------------ ordering
    async def create_lead_or_order(self, phone: str, items: list[OrderItem],
                                   comment: str = "", address: str = "") -> dict:
        self._local_seq += 1
        number = f"ЛОКАЛ-{time.strftime('%Y%m%d')}-{self._local_seq:03d}"
        return {
            "ok": True,
            "number_1c": number,
            "number": number,
            "standalone": True,
            "phone": phone,
            "items": [{"article": i.article, "name": i.name, "quantity": i.quantity,
                       "price": i.price, "sum": i.sum} for i in items],
            "comment": comment,
            "delivery_address": address,
        }


# --------------------------------------------------------------- 1C adaptation
class OneCCatalogProvider(BaseCatalogProvider):
    def __init__(self, one_c_client, submode: str = "http_service"):
        self.client = one_c_client
        self.mode = "one_c_odata" if submode == "odata" else "one_c_http"

    async def get_product_price_and_stock(self, sku_or_name: str,
                                          params: dict | None = None) -> ProductStock:
        return await self.client.get_product_price_and_stock(sku_or_name, params)

    async def create_lead_or_order(self, phone: str, items: list[OrderItem],
                                   comment: str = "", address: str = "") -> dict:
        return await self.client.create_lead_or_order(phone, items, comment, address)

    async def ping(self) -> dict:
        return await self.client.ping()

    async def aclose(self) -> None:
        await self.client.aclose()


# --------------------------------------------------------------------- factory
def build_catalog_provider(settings, storage: Storage, one_c_client) -> BaseCatalogProvider:
    mode = settings.catalog.mode
    if mode in ("one_c_http", "one_c_odata"):
        submode = "odata" if mode == "one_c_odata" else "http_service"
        settings.one_c.mode = submode
        return OneCCatalogProvider(one_c_client, submode)
    return LocalCatalogProvider(storage, settings.catalog)
