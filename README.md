# Voice Sales Agent — голосовой AI-продавец с интеграцией в 1С

Автономное настольное Windows-приложение: принимает входящие SIP-звонки, ведёт
диалог продаж по управляемому сценарию, в реальном времени запрашивает остатки и
цены в 1С (HTTP-сервис или OData), называет точную стоимость и локально хранит
историю звонков, аудиозаписи и структурированные заказы.

## Архитектура

```
SIP-транк (Zadarma / MTT / MegaFon / Mango)
        │  RTP 8/16 kHz PCM
        ▼
telephony/sip_agent.py ── PJSIP (pjsua2), headless RTP-порты, без звуковой карты
        │
        ▼
voice/voice_engine.py ── кольцевой буфер → VAD → STT → диалог → TTS, barge-in
        │                   silero-vad       Yandex v3 / faster-whisper / F5-TTS
        ▼
brain/dialog_manager.py ── state machine + slot filling + function calling
        │                   LLM: Groq Llama-3.3-70B / любой OpenAI-совместимый
        ▼
integrations/one_c_client.py ── HTTP-сервис 1С / стандартный OData
        │
        ▼
core/storage.py ── SQLite (WAL): calls, clients, orders, transcripts
records/*.wav   ── микшированные записи разговоров
ui/ (PySide6)   ── реестр звонков, сценарий, 1С, телефония и голос
```

## Open-source доноры и библиотеки

| Подсистема | Решение | Репозиторий / пакет |
|---|---|---|
| SIP/RTP | PJSIP, биндинги `pjsua2` | github.com/pjsip/pjproject |
| VAD + barge-in | Silero VAD (ONNX/torch, энергетический fallback) | github.com/snakers4/silero-vad |
| STT локально | Faster-Whisper (CTranslate2) | github.com/SYSTRAN/faster-whisper |
| STT/TTS облако | Yandex SpeechKit v3 (gRPC streaming) | grpcio + yandexcloud |
| TTS локально | F5-TTS (zero-shot клон голоса) | github.com/SWivid/F5-TTS |
| LLM | Groq Llama-3.3-70B через OpenAI-совместимый API | api.groq.com |
| GUI | PySide6 (Qt 6) | qt.io |
| Упаковка | PyInstaller onefile | pyinstaller.org |

Тяжелые ML-зависимости подключаются опционально: без ключей и моделей приложение
работает на mock-провайдерах STT/TTS и детерминированном fallback диалога.

## Структура

```
app/
  config.py              настройки (JSON в %APPDATA%/AISalesAgent), env-override
  main.py / __main__.py  точка входа, Qt + поток движка
  core/
    engine.py            оркестрация CallEngine + QThread-обёртка
    storage.py           SQLite-схема и репозиторий
    ring_buffer.py       потокобезопасный аудио-буфер (pre-roll VAD)
    audio.py             PCM-утилиты, WAV, микширование, ресемплинг
    seed.py              демо-данные первого запуска
  voice/
    vad.py               Silero VAD + energy fallback, события start/end
    stt.py               Yandex v3 / faster-whisper / mock
    tts.py               Yandex v3 / F5-TTS / mock
    voice_engine.py      realtime-пайплайн, barge-in, филлеры, запись
  brain/
    llm.py               стриминг SSE, сборка tool_calls, отмена по barge-in
    tools.py             JSON-схемы инструментов и исполнитель (1С)
    dialog_manager.py    КА-автомат состояний, слоты, возражения, fallback
    prompts.py           системный промпт продавца
  integrations/
    one_c_client.py      httpx, 1.5 с таймаут, ретраи, circuit breaker, OData
  telephony/
    sip_agent.py         pjsua2: регистрация, автоответ, двунаправленный media port
  ui/                    PySide6: 4 вкладки, master-detail, QSS Industrial Dark Slate
  resources/style.qss    единая дизайн-система (палитра, состояния, 4px скроллбары)
tests/                   35 pytest-кейсов + offscreen UI smoke + рендер скриншотов
VoiceSalesAgent.spec     PyInstaller onefile
```

## Запуск в разработке

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

python run.py                     # GUI
python -m pytest tests            # 35 тестов, GUI не требуется
```

Headless-проверка интерфейса (Linux/CI): см. `tests/stubs/generate_stubs.py` —
генерирует no-op заглушки системных библиотек для offscreen-плагина Qt.

## Сборка .exe (Windows)

```powershell
pip install -r requirements.txt
powershell -File scripts/build_exe.ps1
# => dist/VoiceSalesAgent.exe — один файл, без консольного окна
```

Данные приложения: `%APPDATA%\AISalesAgent\` — `settings.json`, `agent.db`,
`records/`, `logs/`. Удаление exe не затрагивает накопленную историю.

## Интеграция с 1С

Два равноправных режима (переключатель во вкладке «Подключение к 1С»):

1. **HTTP-сервис** — рекомендуемый: кастомный сервис `/hs/ai_sales/` отдаёт JSON
   с динамической ценой и остатками по складам. Контракт и пример BSL-кода:
   [`docs/1c_http_service_contract.md`](docs/1c_http_service_contract.md).
2. **OData** — стандартный интерфейс `/odata/standard.odata/` без доработки
   конфигурации; имена реквизитов маппятся в интерфейсе.

Авторизация — Basic Auth технического пользователя. Сетевые сбои не роняют
диалог: ретраи с backoff, circuit breaker, вежливый fallback в речи.

## Дизайн-система

Industrial Dark Slate: канвас `#0F1115`, поверхности `#16191F`, единственный
акцент — изумруд `#10B981`. Монохромные stroke-иконки 14–16px (без эмодзи),
строки таблиц 32px, кнопки 28–30px, радиусы 4–6px, ультратонкие скроллбары 4px,
без градиентов и свечения. Все токены продублированы в `app/theme.py` для
элементов, рисуемых через QPainter.
