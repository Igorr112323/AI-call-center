"""Domain dataclasses shared across telephony, voice pipeline, brain and storage."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class CallStatus(str, enum.Enum):
    ONGOING = "ongoing"
    ORDER = "order"            # заказ оформлен
    CONSULTATION = "consultation"
    FAILED = "failed"          # срыв

    @property
    def label_ru(self) -> str:
        return {
            CallStatus.ONGOING: "Идёт разговор",
            CallStatus.ORDER: "Заказ оформлен",
            CallStatus.CONSULTATION: "Консультация",
            CallStatus.FAILED: "Срыв",
        }[self]


class Speaker(str, enum.Enum):
    CLIENT = "client"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass
class TranscriptLine:
    speaker: Speaker
    text: str
    started_at: float          # seconds since call start
    confidence: float = 1.0


@dataclass
class OrderItem:
    article: str
    name: str
    quantity: float
    price: float
    unit: str = "шт"

    @property
    def sum(self) -> float:
        return round(self.quantity * self.price, 2)


@dataclass
class Order:
    id: int | None = None
    call_id: int | None = None
    number_1c: str | None = None
    posted: bool = False
    delivery_address: str = ""
    comment: str = ""
    items: list[OrderItem] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(i.sum for i in self.items), 2)


@dataclass
class CallRecord:
    id: int | None
    phone: str
    started_at: datetime
    duration_sec: int = 0
    status: CallStatus = CallStatus.ONGOING
    summary: str = ""
    record_path: str | None = None
    client_id: int | None = None
    source: str = "sip"          # sip | local | text | demo
    transcript: list[TranscriptLine] = field(default_factory=list)
    order: Order | None = None


@dataclass
class ProductStock:
    """Normalized answer from 1C regardless of OData / HTTP-service mode."""
    found: bool
    article: str = ""
    name: str = ""
    price: float = 0.0
    stock: float = 0.0
    warehouse: str = ""
    raw: dict = field(default_factory=dict)
