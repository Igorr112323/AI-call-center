"""Prompt construction: persona, slot-filling discipline, objection playbook."""
from __future__ import annotations

from ..config import ScenarioConfig

GREETING = "Компания «Промпривод», добрый день. Чем могу помочь?"

FILLERS = [
    "Так, сейчас проверю по базе.",
    "Минутку, смотрю остаток.",
    "Секунду, уточняю цену.",
]


def build_system_prompt(scenario: ScenarioConfig) -> str:
    slots_lines = "\n".join(
        f"- {s.name} ({s.data_type}, {'обязательно' if s.required else 'не обязательно'}): {s.question}"
        for s in scenario.slots
    )
    objection_lines = "\n".join(
        f"- если клиент говорит «{o['trigger']}»: {o['response']}"
        for o in scenario.objections
    )
    return (
        f"{scenario.persona}\n\n"
        "ПРАВИЛА ДИАЛОГА:\n"
        "- Реплики короке 35 слов, разговорный язык, без списков и канцелярита.\n"
        "- Цифры цен и остатков называй ТОЛЬКО из результата инструмента check_catalog_and_price, "
        "никогда не придумывай их.\n"
        "- Каждый распознанный параметр сразу фиксируй инструментом collect_slots.\n"
        "- За раз задавай не более одного вопроса.\n"
        "- Когда все обязательные параметры собраны — вызови check_catalog_and_price и назови точную цену.\n"
        "- После согласия клиента вызови create_order и подтверди номер заказа.\n\n"
        f"СЛОТЫ ЗАКАЗА:\n{slots_lines}\n\n"
        f"РАБОТА С ВОЗРАЖЕНИЯМИ:\n{objection_lines}\n"
    )
