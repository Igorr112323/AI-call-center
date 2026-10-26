"""Performance and memory benchmarks for the offline sales engine.

Runs fully offline (no LLM key, no SIP, no audio): fuzzy catalog lookup
latency, end-to-end brain-turn latency, 50 sequential text calls for leak
detection, and SQLite WAL/PRAGMA verification.

Usage:  .venv/bin/python scripts/benchmark_engine.py
Exits non-zero when a hard budget is violated so CI can gate on it.
"""
from __future__ import annotations

import gc
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import AppSettings  # noqa: E402
from app.core.engine import CallEngine  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.integrations.catalog_provider import LocalCatalogProvider  # noqa: E402
from app.paths import preset_path  # noqa: E402

LATENCY_BUDGET_MS = 15.0
CALL_COUNT = 50
MEMORY_BUDGET_MB = 5.0           # RSS growth across 50 calls (post malloc_trim)
TRACEMALLOC_BUDGET_MB = 2.0      # net persistent Python allocations

# Queries are derived from real preset rows below, so the benchmark stays
# valid when the preset generator changes its parameter grid.
_QUERIES: list[str] = []


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


def _rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0  # non-Linux fallback


def _malloc_trim() -> None:
    """Release free glibc arenas back to the OS (no-op off Linux/glibc)."""
    try:
        ctypes = __import__("ctypes")
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


async def _run() -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="bench-"))
    settings = AppSettings.load(workdir / "settings.json")
    settings.sip.enabled = False
    settings.tts.provider = "mock"
    settings.stt.provider = "mock"

    storage = Storage(workdir / "bench.db")
    provider = LocalCatalogProvider(storage, settings.catalog)
    provider.import_file(preset_path("actuators_catalog.xlsx"))
    engine = CallEngine(settings, storage, workdir)

    from app.integrations.catalog_provider import extract_params
    queries = []
    for row in provider.preview(limit=5):
        params = extract_params(row["name"])
        if params.get("voltage") and params.get("stroke_mm"):
            queries.append(
                f"нужен актуатор {params['voltage']} вольт, "
                f"ход {params['stroke_mm']} мм")
    assert len(queries) >= 3, "preset did not yield parameter-based queries"
    globals()["_QUERIES"] = queries

    report: dict = {}

    # ------------------------------------------------------------- PRAGMAs
    mode = storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
    sync = storage._conn.execute("PRAGMA synchronous").fetchone()[0]
    report["sqlite"] = {"journal_mode": mode, "synchronous": sync}

    # ------------------------------------------------------- fuzzy latency
    fuzzy_ms: list[float] = []
    for _ in range(40):
        for query in _QUERIES:
            t0 = time.perf_counter()
            product = provider.lookup_product(query)
            fuzzy_ms.append((time.perf_counter() - t0) * 1000)
            assert product.found, f"preset lookup failed for {query!r}"
    report["fuzzy_lookup_ms"] = {
        "p50": round(statistics.median(fuzzy_ms), 3),
        "p95": round(_percentile(fuzzy_ms, 95), 3),
        "max": round(max(fuzzy_ms), 3),
        "samples": len(fuzzy_ms),
    }

    # -------------------------------------------------- brain-turn latency
    turn_ms: list[float] = []
    for _ in range(20):
        call = await engine.start_text_call("+7 (999) 000-BENCH")
        for query in _QUERIES[:3]:
            t0 = time.perf_counter()
            await engine.send_text_utterance(query)
            turn_ms.append((time.perf_counter() - t0) * 1000)
        await engine.end_text_call()
    report["brain_turn_ms"] = {
        "p50": round(statistics.median(turn_ms), 3),
        "p95": round(_percentile(turn_ms, 95), 3),
        "max": round(max(turn_ms), 3),
        "samples": len(turn_ms),
    }

    # --------------------------------------------- leak soak test
    # Warm the caches first (SQLite WAL/page cache, font subsets, regex
    # compilation): cold-start allocation is bounded warm-up, not a leak.
    for i in range(CALL_COUNT):
        await engine.start_text_call(f"+7 (999) 000-W{i:03d}")
        await engine.send_text_utterance(
            "Нужен актуатор на 24 вольта, ход 300 мм, одна штука")
        await engine.send_text_utterance("Да, оформляем заказ")
        await engine.end_text_call()
    gc.collect()
    _malloc_trim()
    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()
    rss_before = _rss_mb()
    # Measured steady-state window: a real leak grows linearly here forever.
    for i in range(CALL_COUNT):
        await engine.start_text_call(f"+7 (999) 000-M{i:03d}")
        await engine.send_text_utterance(
            "Нужен актуатор на 24 вольта, ход 300 мм, одна штука")
        await engine.send_text_utterance("Да, оформляем заказ")
        await engine.end_text_call()
    gc.collect()
    snapshot_after = tracemalloc.take_snapshot()
    # NET growth (positive minus released), not the gross positive-only sum
    persistent_kb = sum(
        stat.size_diff for stat in snapshot_after.compare_to(snapshot_before, "lineno")
    ) / 1024
    # stop tracemalloc BEFORE trimming: its live trace storage would otherwise
    # pin glibc arenas and inflate the RSS leak reading
    tracemalloc.stop()
    gc.collect()
    _malloc_trim()  # return glibc arenas so RSS reflects real leaks only
    rss_after = _rss_mb()

    report["soak"] = {
        "warmup_calls": CALL_COUNT,
        "measured_calls": CALL_COUNT,
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "rss_growth_mb": round(rss_after - rss_before, 1),
        "persistent_python_kb": round(persistent_kb, 1),
        "catalog_rows": storage.count_catalog(),
        "calls_in_db": storage.stats()["total"],
        "wal_size_kb": (workdir / "bench.db-wal").stat().st_size // 1024
        if (workdir / "bench.db-wal").exists() else 0,
    }

    await engine.llm.aclose()
    await engine.one_c.aclose()
    await engine.dispatcher.aclose()
    storage.close()
    return report


def _evaluate(report: dict) -> list[str]:
    failures = []
    if report["fuzzy_lookup_ms"]["p95"] >= LATENCY_BUDGET_MS:
        failures.append(
            f"fuzzy p95 {report['fuzzy_lookup_ms']['p95']}ms >= {LATENCY_BUDGET_MS}ms")
    if report["brain_turn_ms"]["p95"] >= LATENCY_BUDGET_MS:
        failures.append(
            f"brain p95 {report['brain_turn_ms']['p95']}ms >= {LATENCY_BUDGET_MS}ms")
    if report["soak"]["rss_growth_mb"] > MEMORY_BUDGET_MB:
        failures.append(
            f"RSS growth {report['soak']['rss_growth_mb']}MB > {MEMORY_BUDGET_MB}MB")
    if report["soak"]["persistent_python_kb"] > TRACEMALLOC_BUDGET_MB * 1024:
        failures.append(
            f"persistent Python allocations "
            f"{report['soak']['persistent_python_kb']}KB exceed budget")
    expected_calls = CALL_COUNT * 2 + 20   # warm-up + measured + latency loops
    if report["soak"]["calls_in_db"] != expected_calls:
        failures.append(
            f"call count in SQLite {report['soak']['calls_in_db']} != {expected_calls}")
    if report["sqlite"]["journal_mode"].lower() != "wal":
        failures.append("SQLite is not in WAL mode")
    if report["sqlite"]["synchronous"] != 1:  # NORMAL
        failures.append("PRAGMA synchronous is not NORMAL (1)")
    return failures


async def run_benchmarks() -> tuple[dict, list[str]]:
    report = await _run()
    return report, _evaluate(report)


def main() -> int:
    import asyncio
    report, failures = asyncio.run(run_benchmarks())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        print("\nBENCHMARK FAILURES:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nAll benchmark budgets passed "
          f"(latency < {LATENCY_BUDGET_MS}ms p95, RSS growth < {MEMORY_BUDGET_MB}MB).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
