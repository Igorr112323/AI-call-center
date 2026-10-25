from app.config import VadConfig
from app.voice.vad import FRAME_MS, UtteranceDetector
from conftest import silence_pcm, tone_pcm


def _frames(pcm: str, sample_rate: int = 16000) -> list[bytes]:
    frame_len = int(sample_rate * 2 * FRAME_MS / 1000)
    return [pcm[i:i + frame_len] for i in range(0, len(pcm), frame_len) if len(pcm[i:]) >= frame_len]


def test_energy_vad_detects_speech_then_silence():
    cfg = VadConfig(provider="energy", energy_threshold_db=-45,
                    min_speech_ms=240, min_silence_ms=300)
    detector = UtteranceDetector(cfg)

    events = []
    for frame in _frames(tone_pcm(500)):
        event = detector.process(frame)
        if event:
            events.append(event.kind)
    assert "start" in events
    assert detector.speech_active is True

    for frame in _frames(silence_pcm(500)):
        event = detector.process(frame)
        if event:
            events.append(event.kind)
    assert events[-1] == "end"
    assert detector.speech_active is False


def test_short_blip_below_min_speech_is_ignored():
    cfg = VadConfig(provider="energy", min_speech_ms=250, min_silence_ms=300)
    detector = UtteranceDetector(cfg)
    events = [e for f in _frames(tone_pcm(90)) if (e := detector.process(f))]
    assert events == []
    assert detector.speech_active is False


def test_reset_clears_state():
    cfg = VadConfig(provider="energy", min_speech_ms=240, min_silence_ms=300)
    detector = UtteranceDetector(cfg)
    for frame in _frames(tone_pcm(400)):
        detector.process(frame)
    assert detector.speech_active is True
    detector.reset()
    assert detector.speech_active is False
