"""Tab 4 — SIP trunk, speech providers, voice character, VAD/barge-in."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from ..config import AppSettings
from . import icons
from .widgets import Badge, Card, FloatEdit, IntEdit, SectionLabel


class TelephonyTab(QWidget):
    saved = Signal()

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Телефония и голос")
        title.setProperty("role", "screenTitle")
        header.addWidget(title)
        header.addStretch(1)
        save = QPushButton("  Сохранить")
        save.setProperty("variant", "primary")
        save.setIcon(icons.icon("check", "#062018"))
        save.clicked.connect(self._save)
        header.addWidget(save)
        root.addLayout(header)

        # --------------------------------------------------------------- SIP
        sip_card = Card()
        sip_head = QHBoxLayout()
        sip_head.addWidget(SectionLabel("SIP-подключение"))
        sip_head.addStretch(1)
        self.sip_status = Badge("Не зарегистрирован", "danger")
        sip_head.addWidget(self.sip_status)
        sip_card.layout().addLayout(sip_head)

        sip_form = QFormLayout()
        sip_form.setSpacing(10)
        self.sip_enabled = QCheckBox("Принимать входящие звонки через SIP")
        self.sip_server = QLineEdit()
        self.sip_port = IntEdit(5060, 1, 65535)
        self.sip_port.setRange(1, 65535)
        self.sip_realm = QLineEdit()
        self.sip_realm.setPlaceholderText("*")
        self.sip_login = QLineEdit()
        self.sip_password = QLineEdit()
        self.sip_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sip_transport = QComboBox()
        self.sip_transport.addItems(["UDP", "TCP", "TLS"])
        self.auto_answer = IntEdit(0, 0, 30000, suffix=" мс")
        sip_form.addRow(self.sip_enabled)
        sip_form.addRow("Сервер", self.sip_server)
        sip_form.addRow("Порт", self.sip_port)
        sip_form.addRow("Realm", self.sip_realm)
        sip_form.addRow("Логин / SIP URI", self.sip_login)
        sip_form.addRow("Пароль", self.sip_password)
        sip_form.addRow("Транспорт", self.sip_transport)
        sip_form.addRow("Автоответ через", self.auto_answer)
        sip_card.layout().addLayout(sip_form)
        root.addWidget(sip_card)

        # ------------------------------------------------------------- voice
        voice_card = Card()
        voice_card.add(SectionLabel("Распознавание и синтез"))
        voice_form = QFormLayout()
        voice_form.setSpacing(10)

        self.stt_provider = QComboBox()
        self.stt_provider.addItems(["mock", "yandex_v3", "faster_whisper"])
        self.tts_provider = QComboBox()
        self.tts_provider.addItems(["mock", "yandex_v3", "f5"])
        self.voice_name = QLineEdit()
        self.voice_name.setPlaceholderText("alena")
        self.emotion = QComboBox()
        self.emotion.addItems(["neutral", "friendly", "strict"])
        self.speed = FloatEdit(1.0, 0.6, 1.8)
        voice_form.addRow("STT-провайдер", self.stt_provider)
        voice_form.addRow("TTS-провайдер", self.tts_provider)
        voice_form.addRow("Голос", self.voice_name)
        voice_form.addRow("Эмоциональная окраска", self.emotion)
        voice_form.addRow("Скорость речи", self.speed)
        voice_card.layout().addLayout(voice_form)
        root.addWidget(voice_card)

        # --------------------------------------------------------------- VAD
        vad_card = Card()
        vad_card.add(SectionLabel("Детектор речи и перебивание"))
        self.barge_in = QCheckBox("Разрешить barge-in (мгновенная остановка бота)")
        vad_card.add(self.barge_in)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)
        slider_label = QLabel("Чувствительность VAD")
        slider_label.setProperty("role", "fieldLabel")
        self.vad_slider = QSlider(Qt.Orientation.Horizontal)
        self.vad_slider.setRange(10, 90)
        self.vad_value = QLabel("0.50")
        self.vad_value.setProperty("role", "mono")
        self.vad_value.setFixedWidth(42)
        self.vad_slider.valueChanged.connect(
            lambda v: self.vad_value.setText(f"{v / 100:.2f}")
        )
        slider_row.addWidget(slider_label)
        slider_row.addWidget(self.vad_slider, 1)
        slider_row.addWidget(self.vad_value)
        vad_card.layout().addLayout(slider_row)

        silence_form = QFormLayout()
        self.min_silence = IntEdit(450, 150, 2000, suffix=" мс")
        self.min_speech = IntEdit(250, 80, 1000, suffix=" мс")
        silence_form.addRow("Тишина для конца фразы", self.min_silence)
        silence_form.addRow("Мин. длительность речи", self.min_speech)
        vad_card.add(silence_form)
        root.addWidget(vad_card, 1)

        # ---------------------------------------------------------- API keys
        api_card = Card()
        api_card.add(SectionLabel("Ключи доступа"))
        api_form = QFormLayout()
        self.yandex_key = QLineEdit()
        self.yandex_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.folder_id = QLineEdit()
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_model = QLineEdit()
        api_form.addRow("Yandex Cloud API-ключ", self.yandex_key)
        api_form.addRow("Yandex Folder ID", self.folder_id)
        api_form.addRow("Groq API-ключ", self.groq_key)
        api_form.addRow("LLM-модель", self.llm_model)
        api_card.layout().addLayout(api_form)
        root.addWidget(api_card)

        hint = QLabel("Изменения SIP применяются после перезапуска службы.")
        hint.setProperty("role", "muted")
        root.addWidget(hint)

        self.load_from(settings)

    def set_registration_state(self, registered: bool, detail: str = "") -> None:
        if registered:
            self.sip_status.setText("Зарегистрирован" + (f" · {detail}" if detail else ""))
            self.sip_status.set_tone("ok")
        else:
            self.sip_status.setText("Не зарегистрирован")
            self.sip_status.set_tone("danger")

    def load_from(self, s: AppSettings) -> None:
        self.sip_enabled.setChecked(s.sip.enabled)
        self.sip_server.setText(s.sip.server)
        self.sip_port.setValue(s.sip.port)
        self.sip_realm.setText(s.sip.realm)
        self.sip_login.setText(s.sip.login)
        self.sip_password.setText(s.sip.password)
        self.sip_transport.setCurrentText(s.sip.transport)
        self.auto_answer.setValue(s.sip.auto_answer_after_ms)

        self.stt_provider.setCurrentText(s.stt.provider)
        self.tts_provider.setCurrentText(s.tts.provider)
        self.voice_name.setText(s.tts.voice)
        self.emotion.setCurrentText(s.tts.emotion)
        self.speed.setValue(s.tts.speed)

        self.barge_in.setChecked(s.vad.barge_in)
        self.vad_slider.setValue(int(s.vad.threshold * 100))
        self.min_silence.setValue(s.vad.min_silence_ms)
        self.min_speech.setValue(s.vad.min_speech_ms)

        self.yandex_key.setText(s.stt.api_key)
        self.folder_id.setText(s.stt.folder_id)
        self.groq_key.setText(s.llm.api_key)
        self.llm_model.setText(s.llm.model)

    def _save(self) -> None:
        s = self.settings
        s.sip.enabled = self.sip_enabled.isChecked()
        s.sip.server = self.sip_server.text().strip()
        s.sip.port = self.sip_port.value()
        s.sip.realm = self.sip_realm.text().strip()
        s.sip.login = self.sip_login.text().strip()
        s.sip.password = self.sip_password.text()
        s.sip.transport = self.sip_transport.currentText()
        s.sip.auto_answer_after_ms = self.auto_answer.value()

        s.stt.provider = self.stt_provider.currentText()
        s.tts.provider = self.tts_provider.currentText()
        s.tts.voice = self.voice_name.text().strip() or "alena"
        s.tts.emotion = self.emotion.currentText()
        s.tts.speed = self.speed.value()
        s.stt.api_key = self.yandex_key.text()
        s.tts.api_key = self.yandex_key.text()
        s.stt.folder_id = self.folder_id.text()
        s.tts.folder_id = self.folder_id.text()

        s.vad.barge_in = self.barge_in.isChecked()
        s.vad.threshold = self.vad_slider.value() / 100.0
        s.vad.min_silence_ms = self.min_silence.value()
        s.vad.min_speech_ms = self.min_speech.value()

        s.llm.api_key = self.groq_key.text()
        s.llm.model = self.llm_model.text().strip() or s.llm.model
        s.save()
        self.saved.emit()
