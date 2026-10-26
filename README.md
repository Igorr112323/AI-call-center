# Voice Sales Agent — голосовой AI-продавец с интеграцией в 1С

Автономное настольное Windows-приложение: принимает входящие SIP-звонки, ведёт
диалог продаж по управляемому сценарию, в реальном времени запрашивает остатки и
цены в 1С (HTTP-сервис или OData), называет точную стоимость и локально хранит
историю звонков, аудиозаписи и структурированные заказы.

## Архитектура

```
SIP-транк (Zadarma / MTT / MegaFon / Mango)      Локальный микрофон (softphone)
        │  RTP 8/16 kHz PCM                              │  sounddevice 16 kHz
        ▼                                                ▼
telephony/sip_agent.py (PJSIP/pjsua2)          telephony/local_audio.py (LocalAudioAgent)
        └───────────────┬────────────────────────────────┘  barge-in через общий VAD
                        ▼
voice/voice_engine.py ── кольцевой буфер → VAD → STT → диалог → TTS
        │                   silero-vad       Yandex v3 / faster-whisper / F5-TTS / mock
                        ▼
brain/dialog_manager.py ── state machine + slot filling + function calling
        │                   LLM Groq Llama-3.3-70B · офлайн-мозг без API-ключа
                        ▼
integrations/catalog_provider.py ── BaseCatalogProvider:
        ├─ LocalCatalogProvider  (SQLite local_catalog, Excel/CSV, fuzzy < 5 мс)
        └─ OneCCatalogProvider   (one_c_client.py: HTTP-сервис 1С / OData)
                        ▼
reporting/quote_generator.py ── PDF «КП / счёт» (reportlab, НДС)
notifications/dispatcher.py  ── Telegram Bot API / вебхук / WhatsApp Green-API
                        ▼
core/storage.py ── SQLite (WAL): calls, clients, orders, transcripts, local_catalog
records/*.wav   ── микшированные записи разговоров; quotes/*.pdf ── сгенерированные КП
ui/ (PySide6)   ── реестр, источник данных/каталог, сценарий, телефония и голос
```

### Автономный режим (без 1С и без облачных API)

Приложение по умолчанию работает полностью офлайн:

1. **Каталог**: импорт `.xlsx/.xls/.csv` drag-and-drop'ом или кнопкой; заголовки
   распознаются по синонимам («Цена/Стоимость/Прайс», «Остаток/Кол-во»…), лишние
   колонки превращаются в параметры товара. Fuzzy-поиск `lookup_product()`
   понимает речь вроде «актуатор на 24 вольта 300 мм» и отвечает менее чем за 5 мс.
2. **Мозг**: при пустом API-ключе LLM включается детерминированный офлайн-диалог
   (slot-filling, возражения, заказ) — демо на ноутбуке не требует интернета.
   Готовые демо-каталоги на 50 позиций лежат в `presets/` (актуаторы, РВД,
   гофрокороба) и загружаются в один клик на вкладке «Источник данных»;
   пересборка: `python scripts/generate_presets.py`.
3. **Softphone**: кнопка «Начать тестовый звонок с микрофона» в верхней панели
   запускает реальный разговор через микрофон/наушники с VU-метром, таймером и
   barge-in; звонок попадает в общий реестр. Без PortAudio кнопка безопасно
   блокируется, остальной функционал доступен.
4. **Текстовый симулятор**: кнопка «Текстовый тест» открывает окно
   «Инспектор диалога / Текстовый симулятор» (750×550) — ввод фраз с клавиатуры
   идёт через тот же `DialogManager`, что и реальный звонок (STT не участвует),
   ответы при наличии аудио озвучивает TTS, иначе тихий режим. Справа — состояние
   КА-автомата, распознанные слоты, бейджи вызовов каталога и RTT в мс. После
   «Положить трубку» звонок сохраняется в SQLite с пометкой «Текстовый тест»,
   заказ получает PDF КП, а кнопка «Открыть КП» в инспекторе активна сразу.
5. **Документы**: по заказу генерируется PDF КП с НДС и отправляется в Telegram,
   универсальный вебхук или WhatsApp (Green-API); без настроек каналов PDF просто
   сохраняется в `quotes/`.

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
| Softphone | sounddevice (PortAudio) | python-sounddevice.org |
| Прайс-листы | openpyxl (.xlsx), встроенный csv | openpyxl.readthedocs.io |
| PDF КП/счета | reportlab | reportlab.com |
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
    catalog_provider.py  BaseCatalogProvider: локальный SQLite + fuzzy / адаптер 1С
    price_parser.py      парсер .xlsx/.xls/.csv, синонимы заголовков, русские числа
    one_c_client.py      httpx, 1.5 с таймаут, ретраи, circuit breaker, OData
  telephony/
    sip_agent.py         pjsua2: регистрация, автоответ, двунаправленный media port
    local_audio.py       LocalAudioAgent: микрофон/наушники, VU-метр, без SIP
    text_simulator.py    TextSimulatorAgent: клавиатура вместо микрофона, mute-safe TTS
  reporting/
    quote_generator.py   PDF «КП / счёт» с НДС (reportlab)
  notifications/
    dispatcher.py        Telegram / вебхук / WhatsApp Green-API
  ui/                    PySide6: 4 вкладки, master-detail, QSS Industrial Dark Slate
    text_call_window.py  окно текстового симулятора: транскрипт, инспектор слотов, RTT
  resources/style.qss    единая дизайн-система (палитра, состояния, 4px скроллбары)
tests/                   140 pytest-кейсов + offscreen UI smoke + рендер скриншотов
presets/                 3 демо-каталога по 50 SKU (один клик во вкладке каталога)
scripts/benchmark_engine.py  латентность, soak 50 звонков, проверка PRAGMA/WAL
AUDIT_REPORT.md          релизный аудит: дефекты, замеры, демо за 60 секунд
VoiceSalesAgent.spec     PyInstaller onefile
```

## Запуск в разработке

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

python run.py                     # GUI
python run.py --selfcheck         # headless-проверка модулей и ресурсов сборки
python -m pytest tests            # 140 тестов, GUI не требуется
python scripts/benchmark_engine.py  # латентность/память/WAL, ненулевой код при провале
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

## Источники данных и интеграция с 1С

Три равноправных режима (вкладка «Источник данных и каталог»):

1. **Автономный прайс-лист (по умолчанию)** — Excel/CSV в локальной SQLite,
   нечеткий поиск без сети, заказы получают номера `ЛОКАЛ-...`, КП выгружается PDF.
2. **HTTP-сервис 1С** — рекомендуемый при интеграции: кастомный сервис
   `/hs/ai_sales/` отдаёт JSON с динамической ценой и остатками по складам.
   Контракт и пример BSL-кода: [`docs/1c_http_service_contract.md`](docs/1c_http_service_contract.md).
3. **OData 1С** — стандартный интерфейс `/odata/standard.odata/` без доработки
   конфигурации; имена реквизитов маппятся в интерфейсе.

Авторизация — Basic Auth технического пользователя. Сетевые сбои не роняют
диалог: ретраи с backoff, circuit breaker, вежливый fallback в речи.

## Дизайн-система

Industrial Dark Slate: канвас `#0F1115`, поверхности `#16191F`, единственный
акцент — изумруд `#10B981`. Монохромные stroke-иконки 14–16px (без эмодзи),
строки таблиц 32px, кнопки 28–30px, радиусы 4–6px, ультратонкие скроллбары 4px,
без градиентов и свечения. Все токены продублированы в `app/theme.py` для
элементов, рисуемых через QPainter.
