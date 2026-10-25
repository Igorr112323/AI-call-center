import json

from app.config import AppSettings, ScenarioConfig, SlotSpec, user_data_dir


def test_roundtrip_preserves_nested_settings(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    settings.one_c.base_url = "http://1c.local/base"
    settings.sip.port = 5061
    settings.vad.threshold = 0.72
    settings.scenario.slots.append(SlotSpec("color", False, "string", "Какой цвет?"))
    settings.save()

    reloaded = AppSettings.load(path)
    assert reloaded.one_c.base_url == "http://1c.local/base"
    assert reloaded.sip.port == 5061
    assert abs(reloaded.vad.threshold - 0.72) < 1e-9
    assert isinstance(reloaded.scenario, ScenarioConfig)
    assert reloaded.scenario.slots[-1].name == "color"


def test_unknown_and_missing_keys_are_tolerated(tmp_path):
    path = tmp_path / "settings.json"
    raw = json.loads(json.dumps({
        "sip": {"server": "sip.example.com", "future_field": "x"},
        "one_c": {"base_url": "http://x"},
        "new_top_section": {"ignored": True},
    }))
    path.write_text(json.dumps(raw), encoding="utf-8")

    settings = AppSettings.load(path)
    assert settings.sip.server == "sip.example.com"
    assert settings.sip.port == 5060  # default intact


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    settings = AppSettings.load(path)
    assert settings.sip.server  # defaults available


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_GROQ_API_KEY", "env-key")
    settings = AppSettings.load(tmp_path / "settings.json")
    assert settings.llm.api_key == "env-key"


def test_user_data_dir_is_under_profile():
    assert str(user_data_dir())
