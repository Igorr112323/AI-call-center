import pytest

from app.telephony import local_audio
from app.telephony.local_audio import build_local_agent


def test_agent_factory_degrades_without_portaudio():
    agent = build_local_agent()
    if local_audio.AVAILABLE:
        pytest.skip("PortAudio present in this environment")
    assert agent.available is False
    with pytest.raises(RuntimeError):
        agent.start(lambda pcm, rate: None)
    agent.stop()  # safe no-op


async def test_null_agent_play_is_noop():
    agent = build_local_agent()
    if local_audio.AVAILABLE:
        pytest.skip("PortAudio present in this environment")
    await agent.play(b"\x00" * 960)  # must not raise


def test_build_local_agent_degrades_when_portaudio_has_no_devices(monkeypatch):
    """Wheel installed, host audio-less (typical CI/RDP session): NullAgent."""
    from app.telephony import local_audio

    class _FakePortAudioError(Exception):
        pass

    class _FakeSoundDevice:
        def query_devices(self, device=None):
            raise _FakePortAudioError("Error querying device -1")

    monkeypatch.setattr(local_audio, "AVAILABLE", True)
    monkeypatch.setattr(local_audio, "sd", _FakeSoundDevice())
    agent = local_audio.build_local_agent()
    assert agent.available is False
    assert isinstance(agent, local_audio.NullLocalAgent)


def test_audio_devices_available_true_with_device_list(monkeypatch):
    from app.telephony import local_audio

    class _FakeSoundDevice:
        def query_devices(self, device=None):
            return [{"name": "fake-mic"}, {"name": "fake-speaker"}]

    monkeypatch.setattr(local_audio, "AVAILABLE", True)
    monkeypatch.setattr(local_audio, "sd", _FakeSoundDevice())
    assert local_audio.audio_devices_available() is True
