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
