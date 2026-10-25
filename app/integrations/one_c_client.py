"""1C:Enterprise connector.

Two transports, one normalized interface:
  * ``http_service`` — custom 1C HTTP service (``/hs/ai_sales/...``) returning JSON;
  * ``odata``        — standard OData interface (``/odata/standard.odata/...``).

Hardened for flaky factory networks: 1.5 s timeout, bounded retries, circuit
breaker, Basic auth with a technical user. No 1C SDK required — plain HTTP.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from urllib.parse import quote, urljoin

import httpx

from ..config import OneCConfig
from ..core.models import OrderItem, ProductStock

log = logging.getLogger(__name__)


class OneCError(Exception):
    """Raised when 1C cannot be reached or returns a malformed response."""


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Tiny async circuit breaker: opens after N failures, cools down, retries once."""

    def __init__(self, fail_threshold: int = 4, cooldown_sec: float = 30.0):
        self.fail_threshold = fail_threshold
        self.cooldown_sec = cooldown_sec
        self._fails = 0
        self._opened_at = 0.0
        self.state = CircuitState.CLOSED

    def _before_call(self) -> None:
        if self.state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_sec:
                self.state = CircuitState.HALF_OPEN
            else:
                raise OneCError("1C circuit breaker open: service unavailable")

    def _on_success(self) -> None:
        self._fails = 0
        self.state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self._fails += 1
        if self._fails >= self.fail_threshold or self.state is CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self._opened_at = time.monotonic()


class OneCClient:
    def __init__(self, config: OneCConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = config
        self.breaker = CircuitBreaker()
        timeout = httpx.Timeout(config.timeout_ms / 1000.0, connect=config.timeout_ms / 1000.0)
        self._client = httpx.AsyncClient(
            base_url=config.base_url or "http://localhost/",
            timeout=timeout,
            auth=(config.username, config.password) if config.username else None,
            headers={"Accept": "application/json", "User-Agent": "AISalesAgent/1.0"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OneCClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ------------------------------------------------------------- low level
    async def _request(self, method: str, url: str, json: dict | None = None) -> dict:
        self.breaker._before_call()
        last_exc: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            try:
                resp = await self._client.request(method, url, json=json)
                resp.raise_for_status()
                data = resp.json()
                self.breaker._on_success()
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                self.breaker._on_failure()
                log.warning("1C request failed (%s/%s) %s %s: %s",
                            attempt + 1, self.cfg.retries + 1, method, url, exc)
                if attempt < self.cfg.retries:
                    await asyncio.sleep(0.15 * (attempt + 1))
        raise OneCError(str(last_exc)) from last_exc

    # ---------------------------------------------------------------- health
    async def ping(self) -> dict:
        """Connectivity probe. Returns warehouse / price-type lists when supported."""
        if self.cfg.mode == "odata":
            data = await self._request("GET", self._odata_url("Catalog_Склады", top=5))
            return {"ok": True, "warehouses": [v.get("Description", "") for v in data.get("value", [])]}
        data = await self._request("GET", "ping")
        return {"ok": True, **(data if isinstance(data, dict) else {})}

    async def get_warehouses(self) -> list[str]:
        if self.cfg.mode == "odata":
            data = await self._request("GET", self._odata_url("Catalog_Склады", top=100))
            return [v.get("Description", "") for v in data.get("value", [])]
        data = await self._request("GET", "warehouses")
        return list(data.get("warehouses", []))

    # ------------------------------------------------------------ catalog API
    async def get_product_price_and_stock(self, sku_or_name: str,
                                          params: dict | None = None) -> ProductStock:
        """Single entry point used by the LLM tool and by slot-filling logic."""
        params = params or {}
        if self.cfg.mode == "odata":
            return await self._odata_lookup(sku_or_name, params)
        payload = {"query": sku_or_name, "params": {k: v for k, v in params.items() if v is not None}}
        data = await self._request("POST", "catalog/lookup", payload)
        return ProductStock(
            found=bool(data.get("found")),
            article=str(data.get("article", "")),
            name=str(data.get("name", "")),
            price=float(data.get("price", 0) or 0),
            stock=float(data.get("stock", 0) or 0),
            warehouse=str(data.get("warehouse", "")),
            raw=data,
        )

    async def create_lead_or_order(self, phone: str, items: list[OrderItem],
                                   comment: str = "", address: str = "") -> dict:
        """Create «Заказ покупателя» (HTTP service) or «Заказ» document via OData."""
        payload = {
            "phone": phone,
            "comment": comment,
            "delivery_address": address,
            "items": [
                {"article": i.article, "name": i.name, "quantity": i.quantity,
                 "price": i.price, "unit": i.unit, "sum": i.sum}
                for i in items
            ],
        }
        if self.cfg.mode == "odata":
            doc = {
                "Phone": phone, "Comment": comment, "DeliveryAddress": address,
                "Date": _odata_now(),
            }
            data = await self._request("POST", self._odata_url("Document_ЗаказПокупателя"), doc)
            ref = data.get("Ref_Key", data.get("Number", ""))
            return {"ok": True, "number_1c": str(data.get("Number", ref)), "ref": ref}
        data = await self._request("POST", "orders/create", payload)
        return {"ok": True, "number_1c": str(data.get("number", data.get("number_1c", ""))), **data}

    # --------------------------------------------------------------- OData bits
    def _odata_url(self, entity: str, *, top: int | None = None,
                   filters: list[str] | None = None, select: list[str] | None = None) -> str:
        base = f"{self.cfg.odata_path.rstrip('/')}/{entity}"
        parts: list[str] = []
        if select:
            parts.append("$select=" + ",".join(select))
        if filters:
            parts.append("$filter=" + " and ".join(filters))
        if top:
            parts.append(f"$top={top}")
        parts.append("$format=json")
        return base + ("?" + "&".join(parts) if parts else "")

    async def _odata_lookup(self, query: str, params: dict) -> ProductStock:
        m = self.cfg.mapping
        name_field = m.get("name", "Наименование")
        article_field = m.get("article", "Артикул")
        flt = [
            f"substringof('{_escape_odata(query)}', {name_field}) "
            f"or substringof('{_escape_odata(query)}', {article_field})"
        ]
        if voltage := params.get("voltage"):
            flt.append(f"НапряжениеВ eq {int(voltage)}")
        if stroke := params.get("stroke_mm"):
            flt.append(f"ХодШтокаМм eq {int(stroke)}")
        url = self._odata_url(
            "Catalog_Номенклатура", top=10, filters=flt,
            select=["Ref_Key", article_field, name_field],
        )
        data = await self._request("GET", url)
        rows = data.get("value", [])
        if not rows:
            return ProductStock(found=False, raw={"query": query})
        top_row = rows[0]
        ref = top_row.get("Ref_Key", "")

        price = 0.0
        try:
            price_url = self._odata_url(
                "InformationRegister_ЦеныНоменклатурыСрезПоследних",
                top=1, filters=[f"Номенклатура_Key eq guid'{ref}'"],
                select=[m.get("price", "Цена")],
            )
            price_data = await self._request("GET", price_url)
            if price_data.get("value"):
                price = float(price_data["value"][0].get(m.get("price", "Цена"), 0) or 0)
        except OneCError:
            log.info("price register unavailable, continuing with zero price")

        stock, warehouse = 0.0, ""
        try:
            stock_url = self._odata_url(
                "InformationRegister_ОстаткиТоваровСрезПоследних",
                top=1, filters=[f"Номенклатура_Key eq guid'{ref}'"],
                select=[m.get("stock", "КоличествоОстаток"), m.get("warehouse", "Склад")],
            )
            stock_data = await self._request("GET", stock_url)
            if stock_data.get("value"):
                row = stock_data["value"][0]
                stock = float(row.get(m.get("stock", "КоличествоОстаток"), 0) or 0)
                warehouse = str(row.get(m.get("warehouse", "Склад"), ""))
        except OneCError:
            log.info("stock register unavailable")

        return ProductStock(
            found=True,
            article=str(top_row.get(article_field, "")),
            name=str(top_row.get(name_field, "")),
            price=price, stock=stock, warehouse=warehouse,
            raw=top_row,
        )


def _escape_odata(value: str) -> str:
    return value.replace("'", "''")


def _odata_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
