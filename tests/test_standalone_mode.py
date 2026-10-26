"""Full standalone application flow: no 1C, no LLM key, no SIP, no cloud."""
import json

from app.config import AppSettings
from app.core.engine import CallEngine
from app.core.models import CallStatus
from app.core.storage import Storage
from app.integrations.catalog_provider import LocalCatalogProvider


async def test_standalone_sale_end_to_end(tmp_path):
    settings = AppSettings.load(tmp_path / "settings.json")
    settings.sip.enabled = False
    settings.stt.provider = "mock"
    settings.tts.provider = "mock"
    settings.tts.sample_rate = 16000
    assert settings.catalog.mode == "local"
    assert settings.llm.api_key == ""

    storage = Storage(tmp_path / "standalone.db")
    storage.replace_catalog([
        {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
         "category": "актуатор", "price": 11500.0, "stock": 14,
         "params_json": json.dumps({"voltage": 24, "stroke_mm": 300})},
    ])

    engine = CallEngine(settings, storage, tmp_path)
    assert isinstance(engine.catalog, LocalCatalogProvider)
    assert engine.local_audio_available in (True, False)  # environment-dependent

    call_id = await engine.start_call("+79182041122", mode="demo")
    quote = await engine.simulate_utterance(
        "Нужен актуатор на 24 вольта, ход 300 мм, одна штука")
    assert "11 500" in quote
    order_reply = await engine.simulate_utterance("Да, оформляем")
    assert "ЛОКАЛ-" in order_reply

    # quote PDF + no-op dispatcher without configured channels
    call = storage.get_call(call_id)
    assert call.order is not None
    result = await engine.resend_offer(call.order.id)
    assert result["pdf"].endswith(".pdf")
    assert result["receipts"] == []

    await engine.end_call()
    finished = storage.get_call(call_id)
    assert finished.status in (CallStatus.ORDER, CallStatus.CONSULTATION)
    assert finished.record_path
    assert len(finished.transcript) >= 4

    await engine.one_c.aclose()
    await engine.llm.aclose()
    await engine.dispatcher.aclose()
    storage.close()
