import httpx
import json
import pytest

from app.config import OneCConfig
from app.core.models import OrderItem
from app.integrations.one_c_client import CircuitBreaker, OneCClient, OneCError


def _http_service_cfg():
    return OneCConfig(base_url="http://1c.local/trade/hs/ai_sales/", mode="http_service",
                      username="bot", password="secret", timeout_ms=1500, retries=1)


async def test_http_service_lookup_and_auth():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        assert request.url.path.endswith("/catalog/lookup")
        return httpx.Response(200, json={
            "found": True, "article": "ACT-24-300", "name": "Актуатор 24В 300мм",
            "price": 11500, "stock": 14, "warehouse": "Основной склад",
        })

    client = OneCClient(_http_service_cfg(), transport=httpx.MockTransport(handler))
    product = await client.get_product_price_and_stock("актуатор", {"voltage": 24})
    assert product.found and product.article == "ACT-24-300"
    assert product.price == 11500 and product.stock == 14
    assert requests[0].url.path == "/trade/hs/ai_sales/catalog/lookup"


async def test_odata_lookup_builds_filter_and_fetches_price_and_stock():
    paths = []

    from urllib.parse import unquote

    def handler(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        query = unquote(str(request.url.query))
        paths.append(path)
        if path.endswith("Catalog_Номенклатура"):
            assert "substringof" in query
            assert "НапряжениеВ eq 24" in query
            return httpx.Response(200, json={"value": [
                {"Ref_Key": "guid-1", "Артикул": "ACT-24-300", "Наименование": "Актуатор 24В"}]})
        if "ЦеныНоменклатурыСрезПоследних" in path:
            return httpx.Response(200, json={"value": [{"Цена": 11500}]})
        if "ОстаткиТоваровСрезПоследних" in path:
            return httpx.Response(200, json={"value": [
                {"КоличествоОстаток": 14, "Склад": "Основной"}]})
        return httpx.Response(404)

    cfg = OneCConfig(base_url="http://1c.local/trade", mode="odata",
                     odata_path="/odata/standard.odata")
    client = OneCClient(cfg, transport=httpx.MockTransport(handler))
    product = await client.get_product_price_and_stock(
        "актуатор", {"voltage": 24, "stroke_mm": 300})
    assert product.found and product.price == 11500 and product.stock == 14
    assert any(p.endswith("Catalog_Номенклатура") for p in paths)


async def test_create_order_http_service_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "number": "Заказ 8412"})

    client = OneCClient(_http_service_cfg(), transport=httpx.MockTransport(handler))
    result = await client.create_lead_or_order(
        "+79180000000", [OrderItem("ACT-24-300", "Актуатор", 2, 11500)], comment="тест")
    assert result["number_1c"] == "Заказ 8412"
    assert captured["body"]["items"][0]["sum"] == 23000


async def test_retry_then_circuit_opens():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        raise httpx.ConnectError("network down")

    breaker = CircuitBreaker(fail_threshold=2, cooldown_sec=30)
    cfg = _http_service_cfg()
    cfg.retries = 1
    client = OneCClient(cfg, transport=httpx.MockTransport(handler))
    client.breaker = breaker

    with pytest.raises(OneCError):
        await client.ping()
    assert attempts["n"] == 2  # initial + one retry
    assert breaker.state.name == "OPEN"

    with pytest.raises(OneCError, match="circuit breaker open"):
        await client.ping()
    assert attempts["n"] == 2  # fast-fail, no network call
