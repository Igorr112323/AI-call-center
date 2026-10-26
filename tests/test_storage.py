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


def test_migration_adds_source_to_legacy_calls_table(tmp_path):
    import sqlite3
    db_file = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(db_file))
    raw.executescript(
        """
        CREATE TABLE calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            phone TEXT NOT NULL,
            started_at TEXT NOT NULL,
            duration_sec INTEGER DEFAULT 0,
            status TEXT NOT NULL,
            summary TEXT DEFAULT '',
            record_path TEXT
        );
        INSERT INTO calls(phone, started_at, status)
        VALUES ('+79990001122', '2026-10-01T10:00:00', 'consultation');
        """
    )
    raw.commit()
    raw.close()

    db = Storage(db_file)  # runs additive _migrate() on open
    call_id = db.create_call("+79990003344", source="text")
    legacy = db.get_call(1)
    new = db.get_call(call_id)
    assert legacy.source == "sip"      # column default for pre-existing rows
    assert new.source == "text"
    db.close()
