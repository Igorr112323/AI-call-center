"""CI-facing performance gates with deliberately relaxed thresholds.

The strict product budgets live in scripts/benchmark_engine.py (15 ms p95,
5 MB RSS); here we only guard against order-of-magnitude regressions on
loaded CI runners.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_engine import run_benchmarks  # noqa: E402
from app.core.storage import Storage  # noqa: E402


async def test_engine_benchmarks_hold_ci_budgets():
    report, hard_failures = await run_benchmarks()

    assert report["sqlite"]["journal_mode"].lower() == "wal"
    assert report["sqlite"]["synchronous"] == 1          # NORMAL
    assert report["fuzzy_lookup_ms"]["p95"] < 50.0
    assert report["brain_turn_ms"]["p95"] < 50.0
    assert report["soak"]["rss_growth_mb"] < 10.0
    assert report["soak"]["persistent_python_kb"] < 2048
    assert report["soak"]["calls_in_db"] == 120
    # the script's own strict evaluation must also be happy on this host
    assert hard_failures == [], hard_failures


def test_sqlite_pragmas_and_wal_checkpoint(tmp_path):
    storage = Storage(tmp_path / "pragma.db")
    mode = storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
    sync = storage._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert mode.lower() == "wal"
    assert sync == 1
    # checkpoint must not raise on an idle database
    storage.checkpoint()
    storage.checkpoint()
    storage.close()
    # closed connection degrades silently instead of raising
    storage.checkpoint()
