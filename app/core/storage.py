"""SQLite persistence (WAL, thread-safe). One local file inside the data dir."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import CallRecord, CallStatus, Order, OrderItem, Speaker, TranscriptLine

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,
    name TEXT DEFAULT '',
    company TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    phone TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_sec INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    summary TEXT DEFAULT '',
    record_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at DESC);

CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    started_at REAL NOT NULL,
    confidence REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_transcript_call ON transcripts(call_id);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES calls(id) ON DELETE SET NULL,
    number_1c TEXT,
    posted INTEGER DEFAULT 0,
    delivery_address TEXT DEFAULT '',
    comment TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    article TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    unit TEXT DEFAULT 'шт'
);

CREATE TABLE IF NOT EXISTS local_catalog (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT DEFAULT '',
    price REAL NOT NULL DEFAULT 0,
    stock INTEGER NOT NULL DEFAULT 0,
    params_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_catalog_category ON local_catalog(category);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            if not getattr(self, "_closed", False):
                self._conn.close()
                self._closed = True

    # ------------------------------------------------------------------ clients
    def upsert_client(self, phone: str, name: str = "", company: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO clients(phone, name, company, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET "
                "name=COALESCE(NULLIF(excluded.name,''), clients.name), "
                "company=COALESCE(NULLIF(excluded.company,''), clients.company) "
                "RETURNING id",
                (phone, name, company, datetime.utcnow().isoformat(timespec="seconds")),
            )
            return int(cur.fetchone()[0])

    # -------------------------------------------------------------------- calls
    def create_call(self, phone: str, record_path: str | None = None) -> int:
        with self._lock:
            client_id = self.upsert_client(phone)
            cur = self._conn.execute(
                "INSERT INTO calls(client_id, phone, started_at, status, record_path) "
                "VALUES (?,?,?,?,?)",
                (client_id, phone, datetime.utcnow().isoformat(timespec="seconds"),
                 CallStatus.ONGOING.value, record_path),
            )
            return int(cur.lastrowid)

    def update_call(self, call_id: int, *, duration_sec: int | None = None,
                    status: CallStatus | None = None, summary: str | None = None,
                    record_path: str | None = None) -> None:
        fields, values = [], []
        for col, val in (("duration_sec", duration_sec), ("status", status.value if status else None),
                         ("summary", summary), ("record_path", record_path)):
            if val is not None:
                fields.append(f"{col}=?")
                values.append(val)
        if not fields:
            return
        values.append(call_id)
        with self._lock:
            self._conn.execute(f"UPDATE calls SET {', '.join(fields)} WHERE id=?", values)

    def add_transcript_line(self, call_id: int, line: TranscriptLine) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO transcripts(call_id, speaker, text, started_at, confidence) "
                "VALUES (?,?,?,?,?)",
                (call_id, line.speaker.value, line.text, line.started_at, line.confidence),
            )

    def save_order(self, order: Order) -> int:
        with self._lock:
            if order.id is None:
                cur = self._conn.execute(
                    "INSERT INTO orders(call_id, number_1c, posted, delivery_address, comment) "
                    "VALUES (?,?,?,?,?)",
                    (order.call_id, order.number_1c, int(order.posted),
                     order.delivery_address, order.comment),
                )
                order.id = int(cur.lastrowid)
            else:
                self._conn.execute(
                    "UPDATE orders SET number_1c=?, posted=?, delivery_address=?, comment=? WHERE id=?",
                    (order.number_1c, int(order.posted), order.delivery_address,
                     order.comment, order.id),
                )
                self._conn.execute("DELETE FROM order_items WHERE order_id=?", (order.id,))
            for item in order.items:
                self._conn.execute(
                    "INSERT INTO order_items(order_id, article, name, quantity, price, unit) "
                    "VALUES (?,?,?,?,?,?)",
                    (order.id, item.article, item.name, item.quantity, item.price, item.unit),
                )
            return order.id

    def get_order_by_call(self, call_id: int) -> Order | None:
        return self._load_order(call_id)

    def get_order(self, order_id: int) -> Order | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                return None
            items_rows = self._conn.execute(
                "SELECT * FROM order_items WHERE order_id=?", (order_id,)
            ).fetchall()
            return Order(
                id=row["id"], call_id=row["call_id"], number_1c=row["number_1c"],
                posted=bool(row["posted"]), delivery_address=row["delivery_address"],
                comment=row["comment"],
                items=[OrderItem(i["article"], i["name"], i["quantity"], i["price"], i["unit"])
                       for i in items_rows],
            )

    def mark_order_posted(self, order_id: int, number_1c: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE orders SET posted=1, number_1c=? WHERE id=?", (number_1c, order_id)
            )

    # ------------------------------------------------------------------ reads
    def list_calls(self, search: str = "", status: CallStatus | None = None) -> list[CallRecord]:
        sql = "SELECT * FROM calls WHERE 1=1"
        params: list = []
        if search:
            sql += " AND (phone LIKE ? OR summary LIKE ?)"
            like = f"%{search}%"
            params += [like, like]
        if status:
            sql += " AND status=?"
            params.append(status.value)
        sql += " ORDER BY started_at DESC LIMIT 500"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [self._hydrate_call(row) for row in rows]

    def get_call(self, call_id: int) -> CallRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            return self._hydrate_call(row) if row else None

    def _hydrate_call(self, row: sqlite3.Row) -> CallRecord:
        t_rows = self._conn.execute(
            "SELECT * FROM transcripts WHERE call_id=? ORDER BY started_at", (row["id"],)
        ).fetchall()
        order = self._load_order(row["id"])
        return CallRecord(
            id=row["id"],
            phone=row["phone"],
            started_at=datetime.fromisoformat(row["started_at"]),
            duration_sec=row["duration_sec"],
            status=CallStatus(row["status"]),
            summary=row["summary"],
            record_path=row["record_path"],
            client_id=row["client_id"],
            transcript=[
                TranscriptLine(Speaker(t["speaker"]), t["text"], t["started_at"], t["confidence"])
                for t in t_rows
            ],
            order=order,
        )

    def _load_order(self, call_id: int) -> Order | None:
        row = self._conn.execute("SELECT * FROM orders WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            return None
        items_rows = self._conn.execute(
            "SELECT * FROM order_items WHERE order_id=?", (row["id"],)
        ).fetchall()
        return Order(
            id=row["id"], call_id=call_id, number_1c=row["number_1c"],
            posted=bool(row["posted"]), delivery_address=row["delivery_address"],
            comment=row["comment"],
            items=[OrderItem(i["article"], i["name"], i["quantity"], i["price"], i["unit"])
                   for i in items_rows],
        )

    # ------------------------------------------------------------ local catalog
    def replace_catalog(self, rows: list[dict]) -> int:
        """Atomic full-reload of the standalone price list."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM local_catalog")
                self._conn.executemany(
                    "INSERT INTO local_catalog(sku, name, category, price, stock, params_json) "
                    "VALUES (:sku, :name, :category, :price, :stock, :params_json)",
                    rows,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            return len(rows)

    def upsert_catalog_row(self, row: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO local_catalog(sku, name, category, price, stock, params_json) "
                "VALUES (:sku, :name, :category, :price, :stock, :params_json) "
                "ON CONFLICT(sku) DO UPDATE SET name=excluded.name, category=excluded.category, "
                "price=excluded.price, stock=excluded.stock, params_json=excluded.params_json",
                row,
            )

    def count_catalog(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM local_catalog").fetchone()[0])

    def list_catalog(self, limit: int = 50, search: str = "") -> list[sqlite3.Row]:
        sql = "SELECT * FROM local_catalog"
        params: list = []
        if search:
            sql += " WHERE name LIKE ? OR sku LIKE ? OR category LIKE ?"
            like = f"%{search}%"
            params = [like, like, like]
        sql += " ORDER BY category, name LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def all_catalog(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT sku, name, category, price, stock, params_json FROM local_catalog"
            ).fetchall()

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) c FROM calls GROUP BY status"
            ).fetchall()
            out = {r["status"]: r["c"] for r in rows}
            out["total"] = sum(out.values())
            return out
