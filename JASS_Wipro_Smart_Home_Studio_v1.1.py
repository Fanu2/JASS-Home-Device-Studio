import sys
import json
import socket
from pathlib import Path

from PySide6.QtCore import Qt, QObject, Signal, QThread
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QColorDialog, QDialog, QFormLayout,
    QLineEdit, QComboBox, QMessageBox, QSpinBox, QGroupBox, QFrame
)

try:
    import tinytuya
except ImportError:
    tinytuya = None


APP_DIR = Path.home() / ".jass_wipro_smart_home"
CONFIG_FILE = APP_DIR / "devices.json"


def load_devices():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def save_devices(devices):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(devices, indent=2), encoding="utf-8")


class Worker(QObject):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.finished.emit(self.fn())
        except Exception as e:
            self.error.emit(str(e))


def run_async(parent, fn, done, failed=None):
    thread = QThread(parent)
    worker = Worker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(done)
    worker.error.connect(failed or (lambda msg: None))
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.error.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread


class AddBulbDialog(QDialog):
    def __init__(self, parent=None, device=None):
        super().__init__(parent)
        self.setWindowTitle("Add Wipro Bulb")
        self.setMinimumWidth(430)
        d = device or {}

        form = QFormLayout(self)

        self.name = QLineEdit(d.get("name", "Wipro Bulb"))
        self.device_id = QLineEdit(d.get("device_id", ""))
        self.ip = QLineEdit(d.get("ip", "Auto"))
        self.local_key = QLineEdit(d.get("local_key", ""))
        self.local_key.setEchoMode(QLineEdit.Password)

        self.version = QComboBox()
        self.version.addItems(["3.3", "3.4", "3.5", "3.1"])
        if d.get("version") in ["3.3", "3.4", "3.5", "3.1"]:
            self.version.setCurrentText(d["version"])

        form.addRow("Name", self.name)
        form.addRow("Device ID", self.device_id)
        form.addRow("Local IP", self.ip)
        form.addRow("Local Key", self.local_key)
        form.addRow("Protocol", self.version)

        hint = QLabel(
            "Discovery can find devices on your LAN, but Tuya Local Keys "
            "usually still need to be supplied."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        form.addRow(buttons)

    def data(self):
        return {
            "name": self.name.text().strip() or "Wipro Bulb",
            "device_id": self.device_id.text().strip(),
            "ip": self.ip.text().strip() or "Auto",
            "local_key": self.local_key.text().strip(),
            "version": self.version.currentText(),
        }


class BulbCard(QFrame):
    changed = Signal()

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device
        self.setObjectName("card")

        root = QVBoxLayout(self)
        title_row = QHBoxLayout()

        self.title = QLabel(device.get("name", "Wipro Bulb"))
        self.title.setObjectName("cardTitle")

        self.status = QLabel("● Unknown")
        self.status.setObjectName("status")

        title_row.addWidget(self.title)
        title_row.addStretch()
        title_row.addWidget(self.status)
        root.addLayout(title_row)

        self.ip_label = QLabel(device.get("ip", "Auto"))
        self.ip_label.setObjectName("subtle")
        root.addWidget(self.ip_label)

        row = QHBoxLayout()
        self.on_btn = QPushButton("ON")
        self.off_btn = QPushButton("OFF")
        row.addWidget(self.on_btn)
        row.addWidget(self.off_btn)
        root.addLayout(row)

        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(1, 100)
        self.brightness.setValue(80)
        root.addWidget(QLabel("Brightness"))
        root.addWidget(self.brightness)

        color_row = QHBoxLayout()
        self.color_btn = QPushButton("Choose RGB Color")
        self.white_btn = QPushButton("White")
        color_row.addWidget(self.color_btn)
        color_row.addWidget(self.white_btn)
        root.addLayout(color_row)

        self.on_btn.clicked.connect(lambda: self.command("on"))
        self.off_btn.clicked.connect(lambda: self.command("off"))
        self.brightness.sliderReleased.connect(
            lambda: self.command("brightness", self.brightness.value())
        )
        self.color_btn.clicked.connect(self.choose_color)
        self.white_btn.clicked.connect(lambda: self.command("white"))

    def set_status(self, text, online=False):
        self.status.setText(("● " if online else "● ") + text)
        self.status.setProperty("online", online)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def choose_color(self):
        c = QColorDialog.getColor(parent=self)
        if c.isValid():
            self.command("color", (c.red(), c.green(), c.blue()))

    def command(self, action, value=None):
        if tinytuya is None:
            QMessageBox.warning(self, "TinyTuya Missing",
                                "Install TinyTuya with:\n\npy -m pip install tinytuya")
            return

        def work():
            d = tinytuya.BulbDevice(
                self.device["device_id"],
                self.device.get("ip", "Auto"),
                self.device["local_key"],
                version=self.device.get("version", "3.3")
            )
            if action == "on":
                return d.turn_on()
            if action == "off":
                return d.turn_off()
            if action == "brightness":
                return d.set_brightness_percentage(int(value))
            if action == "color":
                r, g, b = value
                return d.set_colour(r, g, b)
            if action == "white":
                return d.set_white(self.brightness.value(), 50)
            return d.status()

        def done(_):
            self.set_status("Online", True)
            self.changed.emit()

        def failed(msg):
            self.set_status("Offline / error", False)
            QMessageBox.warning(self, "Bulb Error", f"{self.title.text()}\n\n{msg}")

        run_async(self, work, done, failed)


class DiscoveryDialog(QDialog):
    discovered = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Discover Wipro / Tuya Devices")
        self.resize(760, 470)

        root = QVBoxLayout(self)
        self.info = QLabel(
            "Scan the current local network. This works when the computer and bulbs "
            "are connected to the same LAN."
        )
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.results = QGridLayout()
        root.addLayout(self.results)

        self.scan_btn = QPushButton("🔎 Scan Network")
        self.scan_btn.setObjectName("primary")
        close_btn = QPushButton("Close")

        buttons = QHBoxLayout()
        buttons.addWidget(self.scan_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self.scan_btn.clicked.connect(self.scan)
        close_btn.clicked.connect(self.reject)

    def clear_results(self):
        while self.results.count():
            item = self.results.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def scan(self):
        if tinytuya is None:
            QMessageBox.warning(
                self, "TinyTuya Missing",
                "Install TinyTuya first:\n\npy -m pip install tinytuya"
            )
            return

        self.scan_btn.setEnabled(False)
        self.info.setText("Scanning your local network...")

        def work():
            # TinyTuya API has varied slightly between releases.
            if hasattr(tinytuya, "deviceScan"):
                return tinytuya.deviceScan(False, 20)
            if hasattr(tinytuya, "scan_devices"):
                return tinytuya.scan_devices()
            raise RuntimeError(
                "This TinyTuya version does not expose a discovery function. "
                "Try updating TinyTuya."
            )

        def done(result):
            self.scan_btn.setEnabled(True)
            self.clear_results()

            if not result:
                self.info.setText(
                    "No Tuya-compatible devices were discovered. "
                    "Make sure the PC and bulbs are on the same 2.4 GHz LAN."
                )
                return

            self.info.setText(f"Discovered {len(result)} device(s).")

            row = 0
            for dev_id, data in result.items():
                ip = data.get("ip", "")
                version = str(data.get("version", "3.3"))
                name = data.get("name") or data.get("product_name") or "Tuya / Wipro device"

                self.results.addWidget(QLabel(name), row, 0)
                self.results.addWidget(QLabel(dev_id), row, 1)
                self.results.addWidget(QLabel(ip), row, 2)
                self.results.addWidget(QLabel(version), row, 3)

                add = QPushButton("Add")
                add.clicked.connect(
                    lambda checked=False, did=dev_id, dat=data:
                    self.add_device(did, dat)
                )
                self.results.addWidget(add, row, 4)
                row += 1

        def failed(msg):
            self.scan_btn.setEnabled(True)
            self.info.setText("Discovery failed.")
            QMessageBox.warning(self, "Discovery Error", msg)

        run_async(self, work, done, failed)

    def add_device(self, dev_id, data):
        device = {
            "name": data.get("name") or data.get("product_name") or "Wipro Bulb",
            "device_id": dev_id,
            "ip": data.get("ip", "Auto"),
            "local_key": data.get("local_key", ""),
            "version": str(data.get("version", "3.3")),
        }
        self.discovered.emit(device)
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JASS Wipro Smart Home Studio")
        self.resize(1080, 760)
        self.devices = load_devices()
        self.cards = []

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("JASS Wipro Smart Home Studio")
        title.setObjectName("title")
        subtitle = QLabel("Local-first control for Wipro / Tuya Wi-Fi bulbs")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.discover_btn = QPushButton("🔎 Discover Devices")
        self.add_btn = QPushButton("＋ Add Manually")
        self.all_on = QPushButton("All ON")
        self.all_off = QPushButton("All OFF")

        for b in (self.discover_btn, self.add_btn, self.all_on, self.all_off):
            header.addWidget(b)

        root.addLayout(header)

        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        root.addLayout(self.grid)

        self.footer = QLabel(
            "Local control • Same Wi-Fi required • No Wipro cloud login required"
        )
        self.footer.setObjectName("footer")
        root.addWidget(self.footer)

        self.discover_btn.clicked.connect(self.open_discovery)
        self.add_btn.clicked.connect(self.add_manual)
        self.all_on.clicked.connect(lambda: self.all_command("on"))
        self.all_off.clicked.connect(lambda: self.all_command("off"))

        self.refresh_cards()

    def refresh_cards(self):
        for card in self.cards:
            card.deleteLater()
        self.cards = []

        for i, device in enumerate(self.devices):
            card = BulbCard(device)
            self.cards.append(card)
            self.grid.addWidget(card, i // 2, i % 2)

    def add_manual(self):
        dlg = AddBulbDialog(self)
        if dlg.exec():
            d = dlg.data()
            if not d["device_id"] or not d["local_key"]:
                QMessageBox.warning(self, "Missing Information",
                                    "Device ID and Local Key are required.")
                return
            self.devices.append(d)
            save_devices(self.devices)
            self.refresh_cards()

    def open_discovery(self):
        dlg = DiscoveryDialog(self)
        dlg.discovered.connect(self.add_discovered)
        dlg.exec()

    def add_discovered(self, device):
        # Avoid duplicate device IDs.
        for existing in self.devices:
            if existing.get("device_id") == device.get("device_id"):
                QMessageBox.information(
                    self, "Already Added",
                    f"{device.get('name')} is already in your studio."
                )
                return

        if not device.get("local_key"):
            # Discovery usually cannot provide the local key.
            dlg = AddBulbDialog(self, device)
            if dlg.exec():
                device = dlg.data()
            else:
                return

        self.devices.append(device)
        save_devices(self.devices)
        self.refresh_cards()

    def all_command(self, action):
        for card in self.cards:
            card.command(action)


STYLES = """
QWidget {
    background: #0b1020;
    color: #e8edf7;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QLabel#title {
    font-size: 22pt;
    font-weight: 700;
}
QLabel#subtitle, QLabel#subtle, QLabel#footer, QLabel#hint {
    color: #8d99ae;
}
QLabel#status {
    color: #f0b35b;
}
QLabel#status[online="true"] {
    color: #54d69b;
}
QFrame#card {
    background: #141b2d;
    border: 1px solid #27324a;
    border-radius: 18px;
}
QLabel#cardTitle {
    font-size: 14pt;
    font-weight: 650;
}
QPushButton {
    background: #1d2840;
    border: 1px solid #35425e;
    border-radius: 9px;
    padding: 9px 14px;
}
QPushButton:hover {
    background: #273653;
}
QPushButton#primary {
    background: #315fca;
    border-color: #4777dc;
}
QLineEdit, QComboBox, QSpinBox {
    background: #101729;
    border: 1px solid #33405b;
    border-radius: 8px;
    padding: 8px;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #29344b;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: #5d8df1;
}
"""

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLES)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
