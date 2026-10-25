from app.core.models import (
    CallStatus, Order, OrderItem, Speaker, TranscriptLine,
)
from app.core.storage import Storage


def test_call_lifecycle_and_hydration(tmp_path):
    db = Storage(tmp_path / "agent.db")
    call_id = db.create_call("+79182041122", record_path="/tmp/x.wav")
    db.add_transcript_line(call_id, TranscriptLine(Speaker.AGENT, "Добрый день", 0.0))
    db.add_transcript_line(call_id, TranscriptLine(Speaker.CLIENT, "Нужен актуатор", 3.0))

    order = Order(call_id=call_id, number_1c="№8412", posted=True,
                  items=[OrderItem("ACT-24-300", "Актуатор 24В", 2, 11500)])
    order_id = db.save_order(order)
    db.update_call(call_id, duration_sec=120, status=CallStatus.ORDER, summary="Актуатор (2 шт)")

    call = db.get_call(call_id)
    assert call.status is CallStatus.ORDER
    assert len(call.transcript) == 2
    assert call.transcript[1].speaker is Speaker.CLIENT
    assert call.order.total == 23000
    assert db.get_order(order_id).number_1c == "№8412"


def test_search_filter_and_stats(tmp_path):
    db = Storage(tmp_path / "agent.db")
    db.create_call("+700000001")
    cid = db.create_call("+700000002")
    db.update_call(cid, status=CallStatus.FAILED, duration_sec=2)

    assert len(db.list_calls()) == 2
    assert len(db.list_calls(status=CallStatus.FAILED)) == 1
    assert len(db.list_calls(search="700000002")) == 1
    stats = db.stats()
    assert stats["total"] == 2 and stats["failed"] == 1


def test_client_upsert_dedupes_phone(tmp_path):
    db = Storage(tmp_path / "agent.db")
    first = db.upsert_client("+700000003", name="")
    second = db.upsert_client("+700000003", name="ООО Завод")
    assert first == second
