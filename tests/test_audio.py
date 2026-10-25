from app.core.audio import (
    format_timecode, mix, pcm_duration_ms, read_wav, resample_linear,
    rms_dbfs, write_wav,
)
from conftest import silence_pcm, tone_pcm


def test_silence_vs_tone_level():
    assert rms_dbfs(silence_pcm(100)) < -100
    assert rms_dbfs(tone_pcm(100)) > -20


def test_resample_doubles_size():
    src = tone_pcm(100, sample_rate=8000)
    out = resample_linear(src, 8000, 16000)
    assert abs(len(out) - len(src) * 2) < 64


def test_wav_roundtrip(tmp_path):
    pcm = tone_pcm(200)
    path = write_wav(tmp_path / "call.wav", pcm, 16000)
    loaded, rate, channels = read_wav(path)
    assert rate == 16000 and channels == 1
    assert loaded == pcm


def test_mix_equal_length():
    a = tone_pcm(50, freq=200)
    b = tone_pcm(50, freq=400)
    mixed = mix(a, b)
    assert len(mixed) == len(a)


def test_duration_and_timecode():
    assert pcm_duration_ms(b"\x00\x00" * 16000, 16000) == 1000
    assert format_timecode(84) == "01:24"
