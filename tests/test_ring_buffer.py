from app.core.ring_buffer import AudioRingBuffer


def test_capacity_evicts_oldest():
    buf = AudioRingBuffer(capacity_bytes=10)
    buf.write(b"12345")
    buf.write(b"67890")
    assert buf.snapshot() == b"1234567890"

    buf.write(b"AB")
    # capacity holds exactly 10 bytes, oldest dropped first
    assert buf.snapshot() == b"34567890AB"


def test_oversized_chunk_keeps_tail():
    buf = AudioRingBuffer(capacity_bytes=4)
    buf.write(b"12345678")
    assert buf.snapshot() == b"5678"


def test_drain_and_clear():
    buf = AudioRingBuffer(capacity_bytes=8)
    buf.write(b"abc")
    assert buf.drain() == b"abc"
    assert buf.size == 0
    buf.write(b"def")
    buf.clear()
    assert buf.snapshot() == b""


def test_bytes_for_ms():
    # 16 kHz, 16-bit mono: 32000 bytes per second
    assert AudioRingBuffer.bytes_for_ms(1000, 16000) == 32000
    assert AudioRingBuffer.bytes_for_ms(30, 16000) == 960
