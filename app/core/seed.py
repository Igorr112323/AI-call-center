"""First-run demo dataset so the registry reads like a real production tool."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .models import CallStatus, Order, OrderItem, Speaker, TranscriptLine
from .storage import Storage

_DEMO_CATALOG = [
    {"sku": "ACT-24-300", "name": "Актуатор линейный 24В, ход 300мм",
     "category": "актуатор", "price": 11500.0, "stock": 14,
     "params": {"voltage": 24, "stroke_mm": 300, "force_n": 500}},
    {"sku": "ACT-12-150", "name": "Актуатор линейный 12В, ход 150мм",
     "category": "актуатор", "price": 4200.0, "stock": 60,
     "params": {"voltage": 12, "stroke_mm": 150, "force_n": 300}},
    {"sku": "ACT-220-400", "name": "Актуатор линейный 220В, ход 400мм",
     "category": "актуатор", "price": 18900.0, "stock": 7,
     "params": {"voltage": 220, "stroke_mm": 400, "force_n": 1200}},
    {"sku": "SRV-750-B", "name": "Сервопривод 750Вт с электромагнитным тормозом",
     "category": "сервопривод", "price": 48000.0, "stock": 2,
     "params": {"power_w": 750}},
    {"sku": "LSE-IP67", "name": "Концевой выключатель IP67 для конвейера",
     "category": "выключатель", "price": 1450.0, "stock": 120,
     "params": {"protection": "IP67"}},
    {"sku": "MTR-15-1500", "name": "Двигатель асинхронный 15кВт, 1500 об/мин",
     "category": "двигатель", "price": 92400.0, "stock": 3,
     "params": {"power_kw": 15, "rpm": 1500}},
]


def seed_local_catalog(storage: Storage) -> int:
    if storage.count_catalog() > 0:
        return 0
    rows = [
        {**{k: v for k, v in item.items() if k != "params"},
         "params_json": json.dumps(item["params"], ensure_ascii=False)}
        for item in _DEMO_CATALOG
    ]
    return storage.replace_catalog(rows)


def seed_if_empty(storage: Storage) -> bool:
    seed_local_catalog(storage)
    if storage.stats().get("total", 0) > 0:
        return False

    now = datetime.now()

    def call(phone: str, minutes_ago: int, duration: int, status: CallStatus,
             summary: str, transcript: list[tuple[str, str, float]],
             order: Order | None = None) -> None:
        started = now - timedelta(minutes=minutes_ago)
        client_id = storage.upsert_client(phone)
        # bypass create_call's timestamp to place history naturally
        row = storage._conn.execute(
            "INSERT INTO calls(client_id, phone, started_at, duration_sec, status, summary, record_path) "
            "VALUES (?,?,?,?,?,?,?)",
            (client_id, phone, started.isoformat(timespec="seconds"),
             duration, status.value, summary, None),
        )
        call_id = int(row.lastrowid)
        for speaker, text, at in transcript:
            storage.add_transcript_line(
                call_id, TranscriptLine(Speaker(speaker), text, at)
            )
        if order:
            order.call_id = call_id
            storage.save_order(order)

    call(
        "+79182041122", 12, 164, CallStatus.ORDER, "Актуатор 24В 300мм (1 шт)",
        [
            ("agent", "Компания «Промпривод», добрый день. Чем могу помочь?", 0.0),
            ("client", "Здравствуйте, нужен актуатор на двадцать четыре вольта, ход триста миллиметров.", 4.0),
            ("agent", "Так, сейчас проверю по базе.", 12.0),
            ("agent", "Артикул ACT-24-300, на основном складе четырнадцать единиц. Цена одиннадцать тысяч пятьсот рублей.", 18.0),
            ("client", "Одна штука, доставка в Краснодар. Давайте оформим.", 46.0),
            ("agent", "Зафиксировал. Заказ номер восемь тысяч четыреста двенадцать, менеджер свяжется для подтверждения доставки.", 58.0),
        ],
        Order(number_1c="№8412", posted=True, delivery_address="Краснодар",
              items=[OrderItem("ACT-24-300", "Актуатор линейный 24В, ход 300мм", 1, 11500)]),
    )

    call(
        "+74951204488", 47, 223, CallStatus.CONSULTATION, "Запрос: концевой выключатель",
        [
            ("agent", "Компания «Промпривод», добрый день.", 0.0),
            ("client", "Подскажите по концевым выключателям для конвейера, нужна пылезащита IP67.", 5.0),
            ("agent", "Минутку, смотрю остаток.", 14.0),
            ("client", "И вопрос по скидке, если возьмём двадцать штук.", 78.0),
            ("agent", "При объёме от десяти единиц согласуем скидку с руководителем, зафиксирую запрос.", 84.0),
        ],
    )

    call(
        "+78612209931", 95, 8, CallStatus.FAILED, "",
        [("agent", "Компания «Промпривод», добрый день.", 0.0)],
    )

    call(
        "+79034561209", 180, 312, CallStatus.ORDER, "Актуатор 12В 150мм (10 шт)",
        [
            ("agent", "Компания «Промпривод», добрый день. Чем могу помочь?", 0.0),
            ("client", "Интересует актуатор на двенадцать вольт, ход сто пятьдесят.", 6.0),
            ("agent", "Секунду, уточняю цену.", 11.0),
            ("agent", "Артикул ACT-12-150, стоимость четыре тысячи двести рублей, в наличии шестьдесят единиц.", 19.0),
            ("client", "Десять штук запишите, заберём самовывозом.", 130.0),
            ("agent", "Оформил, заказ восемь тысяч четыреста пятнадцать. Спасибо за обращение.", 140.0),
        ],
        Order(number_1c="№8415", posted=True,
              items=[OrderItem("ACT-12-150", "Актуатор линейный 12В, ход 150мм", 10, 4200)]),
    )

    call(
        "+79173348812", 260, 198, CallStatus.CONSULTATION, "Запрос: актуатор",
        [
            ("agent", "Компания «Промпривод», добрый день.", 0.0),
            ("client", "Хотел сравнить актуаторы на двести двадцать вольт, что есть в наличии.", 7.0),
            ("agent", "Так, сейчас проверю по базе.", 13.0),
            ("client", "Я подумаю и перезвоню на следующей неделе.", 150.0),
            ("agent", "Конечно. Зафиксирую цену и наличие до конца дня и пришлю предложение письменно.", 156.0),
        ],
    )

    call(
        "+74999027746", 320, 274, CallStatus.ORDER, "Сервопривод 750Вт (2 шт)",
        [
            ("agent", "Компания «Промпривод», добрый день. Чем могу помочь?", 0.0),
            ("client", "Нужен сервопривод семьсот пятьдесят ватт с тормозом, две штуки.", 8.0),
            ("agent", "Минутку, смотрю остаток.", 15.0),
            ("agent", "SRV-750-B, две единицы в резерве, цена сорок восемь тысяч за штуку.", 24.0),
            ("client", "Бронируйте обе, реквизиты пришлю на почту.", 200.0),
            ("agent", "Бронь поставил, заказ восемь тысяч четыреста восемнадцать. Ждём реквизиты.", 208.0),
        ],
        Order(number_1c=None, posted=False,
              items=[OrderItem("SRV-750-B", "Сервопривод 750Вт с электромагнитным тормозом", 2, 48000)]),
    )
    return True
