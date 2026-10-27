"""Application configuration.

Single JSON file in the per-user data directory, tolerant to schema changes
(unknown keys are kept, missing keys get defaults). Secrets are stored locally
on the company machine; ``AI_*`` environment variables override file values.
"""
from __future__ import annotations

import json
import os
import sys
import typing
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def user_data_dir() -> Path:
    """Per-user writable directory (next to exe is not writable under Program Files)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / "AISalesAgent"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "AISalesAgent"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aisalesagent"
    return path


@dataclass
class SipConfig:
    enabled: bool = False
    server: str = "sip.zadarma.com"
    port: int = 5060
    realm: str = ""
    login: str = ""
    password: str = ""
    display_name: str = "AI Sales"
    auto_answer_after_ms: int = 0
    transport: str = "UDP"  # UDP | TCP | TLS
    sample_rate: int = 16000


@dataclass
class OneCConfig:
    base_url: str = ""                       # e.g. http://192.168.1.100/trade_base/hs/ai_sales/
    mode: str = "http_service"               # http_service | odata
    odata_path: str = "/odata/standard.odata"
    username: str = ""
    password: str = ""
    timeout_ms: int = 1500
    retries: int = 1
    # Field mapping for OData mode: internal name -> 1C metadata name
    mapping: dict[str, str] = field(default_factory=lambda: {
        "article": "Артикул",
        "name": "Наименование",
        "stock": "КоличествоОстаток",
        "price": "Цена",
        "warehouse": "Склад",
    })


@dataclass
class SttConfig:
    provider: str = "mock"                   # yandex_v3 | faster_whisper | mock
    api_key: str = ""
    folder_id: str = ""                      # Yandex Cloud folder id
    language: str = "ru-RU"
    model: str = "whisper-1"                 # faster-whisper model size
    device: str = "cpu"                      # cpu | cuda


@dataclass
class TtsConfig:
    provider: str = "auto"   # auto | edge | piper | sapi | mock | yandex_v3 | f5
    api_key: str = ""
    folder_id: str = ""
    voice: str = "alena"
    speed: float = 1.0
    emotion: str = "neutral"                 # neutral | friendly | strict
    sample_rate: int = 24000
    fillers: list[str] = field(default_factory=lambda: [
        "Так, сейчас проверю по базе.",
        "Минутку, смотрю остаток.",
        "Секунду, уточняю цену.",
    ])


@dataclass
class LlmConfig:
    provider: str = "groq"                   # groq | openai_compatible
    base_url: str = "https://api.groq.com/openai/v1"
    api_key: str = ""
    model: str = "llama-3.3-70b-versatile"
    temperature: float = 0.4
    max_tokens: int = 420
    timeout_ms: int = 4000


@dataclass
class VadConfig:
    provider: str = "silero"                 # silero | energy
    threshold: float = 0.5                   # 0..1, barge-in sensitivity
    energy_threshold_db: float = -45.0
    min_speech_ms: int = 250
    min_silence_ms: int = 450
    barge_in: bool = True
    preroll_ms: int = 300                    # ring buffer pre-roll before speech


@dataclass
class SlotSpec:
    name: str
    required: bool
    data_type: str          # string | integer | float
    question: str


@dataclass
class ScenarioConfig:
    persona: str = (
        "Ты — менеджер по продажам промышленного оборудования в B2B-компании. "
        "Говоришь кратко, уверенно, как живой человек: без канцелярита и шаблонных фраз. "
        "Не называй цены из памяти — любую цену и наличие подтверждай инструментом check_catalog_and_price. "
        "Если данных не хватает — задай один конкретный вопрос. В конце разговора зафиксируй заказ или согласуй следующий шаг."
    )
    slots: list[SlotSpec] = field(default_factory=lambda: [
        SlotSpec("category", True, "string", "Какое оборудование вас интересует?"),
        SlotSpec("voltage", True, "integer", "Какое напряжение питания нужно?"),
        SlotSpec("stroke_mm", True, "integer", "Какой ход штока, в миллиметрах?"),
        SlotSpec("force_n", False, "integer", "Какое рабочее усилие требуется?"),
        SlotSpec("quantity", True, "integer", "Сколько единиц планируете заказывать?"),
        SlotSpec("delivery_city", False, "string", "В какой город нужна доставка?"),
    ])
    objections: list[dict[str, str]] = field(default_factory=lambda: [
        {"trigger": "дорого", "response": "Понимаю. Давайте сравним с аналогом и посчитаем стоимость за единицу с учётом срока службы."},
        {"trigger": "нет в наличии", "response": "Проверяю ближайшее поступление и предложу альтернативу с теми же параметрами."},
        {"trigger": "скидку", "response": "При объёме от десяти единиц согласуем скидку с руководителем — могу сразу зафиксировать запрос."},
        {"trigger": "подумаю", "response": "Конечно. Зафиксирую для вас цену и наличие до конца дня и пришлю предложение письменно."},
        {"trigger": "робот", "response": "Я голосовой ассистент компании: работаю по актуальному прайсу и складу и сразу фиксирую заявку."},
        {"trigger": "человек", "response": "Запишу ваш вопрос и передам живому менеджеру — он перезвонит с готовой ценой и наличием."},
    ])


@dataclass
class CatalogConfig:
    # local (standalone Excel/CSV) | one_c_http | one_c_odata
    mode: str = "local"
    last_import_path: str = ""
    fuzzy_min_score: int = 40          # 0..100, порог уверенности совпадения
    quote_company_name: str = "ООО «Промпривод»"
    quote_inn: str = "2310000000"
    quote_kpp: str = "231001001"
    quote_ogrn: str = "1232300000000"
    quote_bank: str = "Краснодарское отделение № 8619 ПАО Сбербанк"
    quote_bank_account: str = "40702810400000012345"
    quote_corr_account: str = "30101810100000000602"
    quote_bik: str = "040349602"
    quote_legal_address: str = "350000, г. Краснодар, ул. Промышленная, 12"
    quote_vat_rate: float = 20.0       # НДС, %; 0 — без НДС


@dataclass
class NotificationConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    whatsapp_endpoint: str = ""        # Green-API / произвольный вебхук
    whatsapp_token: str = ""
    generic_webhook_url: str = ""
    enabled: bool = False


@dataclass
class AppSettings:
    sip: SipConfig = field(default_factory=SipConfig)
    one_c: OneCConfig = field(default_factory=OneCConfig)
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    scenario: ScenarioConfig = field(default_factory=ScenarioConfig)

    # runtime, not persisted
    _path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        path = path or (user_data_dir() / "settings.json")
        if not path.exists():
            settings = cls()
            settings._path = path
            _apply_env_overrides(settings)
            return settings
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = cls()
            settings._path = path
            _apply_env_overrides(settings)
            return settings
        settings = _from_dict(cls, raw)
        settings._path = path
        # Legacy builds shipped the tone stub as the default; upgrade users
        # to the native offline voice. Explicit "mock" can still be reselected
        # manually in settings or forced by tests after load().
        if isinstance(raw.get("tts"), dict) and raw["tts"].get("provider") == "mock":
            settings.tts.provider = "auto"
        _apply_env_overrides(settings)
        return settings

    def save(self, path: Path | None = None) -> Path:
        path = path or self._path or (user_data_dir() / "settings.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._path = path
        return path


def _from_dict(root_cls: type, raw: dict[str, Any]):
    """Tolerant dataclass loader: ignores unknown keys, fills missing with defaults."""
    hints = typing.get_type_hints(root_cls)
    kwargs: dict[str, Any] = {}
    for f in fields(root_cls):
        if f.name.startswith("_") or f.name not in raw:
            continue
        ftype = hints.get(f.name, str)
        value = raw[f.name]
        args = typing.get_args(ftype)
        if ftype == SlotSpec or (typing.get_origin(ftype) is list and args and args[0] == SlotSpec):
            kwargs[f.name] = [SlotSpec(**item) for item in value]
        elif isinstance(ftype, type) and dataclass_is_nested(ftype):
            kwargs[f.name] = _from_dict(ftype, value)
        else:
            kwargs[f.name] = value
    return root_cls(**kwargs)


def dataclass_is_nested(cls: type) -> bool:
    return hasattr(cls, "__dataclass_fields__")


def _apply_env_overrides(s: AppSettings) -> None:
    """CI/12-factor overrides for secrets so they never need to touch disk."""
    env = os.environ
    if v := env.get("AI_GROQ_API_KEY"):
        s.llm.api_key = v
    if v := env.get("AI_YANDEX_API_KEY"):
        s.stt.api_key = v
        s.tts.api_key = v
    if v := env.get("AI_YANDEX_FOLDER_ID"):
        s.stt.folder_id = v
        s.tts.folder_id = v
    if v := env.get("AI_1C_USERNAME"):
        s.one_c.username = v
    if v := env.get("AI_1C_PASSWORD"):
        s.one_c.password = v
    if v := env.get("AI_SIP_PASSWORD"):
        s.sip.password = v
    if v := env.get("AI_TELEGRAM_BOT_TOKEN"):
        s.notifications.telegram_bot_token = v
    if v := env.get("AI_TELEGRAM_CHAT_ID"):
        s.notifications.telegram_chat_id = v
