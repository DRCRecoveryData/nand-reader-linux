#!/usr/bin/env python3
"""PyQt6 front end for the Soft-Center NAND reader on Linux.

Reads an ID from each bank and dumps a page range, driving nand_linux.Reader.
Long operations run on a worker thread so the window stays live.
"""

import os
import sys
import threading
import time

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl
import payloads_check


class Worker(QObject):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)
    id_row = pyqtSignal(int, str)
    progress = pyqtSignal(int)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.fn(self)
            self.done.emit(True, "")
        except Exception as exc:  # surfaced in the UI, not swallowed
            self.done.emit(False, str(exc))


class ReaderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NAND Reader 2.0 — Linux")
        self.resize(760, 620)
        self.reader = None
        self.thread = None

        root = QWidget()
        layout = QVBoxLayout(root)

        status = QLabel(self._device_status())
        status.setStyleSheet("font-weight: bold;")
        layout.addWidget(status)

        payload_box = QGroupBox("Payloads")
        pl = QHBoxLayout(payload_box)
        self.payload_dir = QLineEdit("payloads")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_payload_dir)
        check = QPushButton("Check")
        check.clicked.connect(self._check_payloads)
        pl.addWidget(self.payload_dir)
        pl.addWidget(browse)
        pl.addWidget(check)
        layout.addWidget(payload_box)

        id_box = QGroupBox("Read ID")
        il = QFormLayout(id_box)
        self.cv = QDoubleSpinBox()
        self.cv.setRange(1.2, 3.3)
        self.cv.setSingleStep(0.05)
        self.cv.setDecimals(2)
        self.cv.setValue(3.30)
        self.banks = QSpinBox()
        self.banks.setRange(1, 4)
        self.banks.setValue(1)
        self.id_btn = QPushButton("Read ID")
        self.id_btn.clicked.connect(self._read_id)
        self.ids = QPlainTextEdit()
        self.ids.setReadOnly(True)
        self.ids.setMaximumHeight(90)
        il.addRow("Rail (V)", self.cv)
        il.addRow("Banks", self.banks)
        il.addRow("", self.id_btn)
        il.addRow("Bank / ID", self.ids)
        layout.addWidget(id_box)

        dump_box = QGroupBox("Dump")
        dl = QFormLayout(dump_box)
        self.out = QLineEdit("out/chip.bin")
        outb = QPushButton("…")
        outb.clicked.connect(self._pick_out)
        outrow = QHBoxLayout()
        outrow.addWidget(self.out)
        outrow.addWidget(outb)
        self.start = QSpinBox()
        self.start.setRange(0, 0x7FFFFFFF)
        self.end = QSpinBox()
        self.end.setRange(1, 0x7FFFFFFF)
        self.end.setValue(4718592)
        self.unit = QSpinBox()
        self.unit.setRange(1, 65535)
        self.unit.setValue(18592)
        self.dbank = QSpinBox()
        self.dbank.setRange(1, 4)
        self.dump_btn = QPushButton("Dump")
        self.dump_btn.clicked.connect(self._dump)
        dl.addRow("Output", outrow)
        dl.addRow("Start page", self.start)
        dl.addRow("End page", self.end)
        dl.addRow("Wire page size", self.unit)
        dl.addRow("Bank", self.dbank)
        dl.addRow("", self.dump_btn)
        layout.addWidget(dump_box)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        layout.addWidget(self.logv, 1)

        self.setCentralWidget(root)

    def _device_status(self):
        try:
            with nl.Reader() as r:
                return f"reader: {r.dev.manufacturer} / {r.dev.product} — claimed"
        except Exception as exc:
            return f"reader: NOT AVAILABLE ({exc})"

    def _log(self, msg):
        self.logv.appendPlainText(msg)

    def _pick_payload_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Payload directory", self.payload_dir.text().strip())
        if d:
            self.payload_dir.setText(d)

    def _pick_out(self):
        p, _ = QFileDialog.getSaveFileName(self, "Dump to", self.out.text())
        if p:
            self.out.setText(p)

    def _check_payloads(self):
        rows, missing = payloads_check.check(self.payload_dir.text().strip())
        for name, note, size, sha, swap in rows:
            self._log(f"{name:<18}{note}")
        self._log("all payloads present" if not missing else "missing: " + ", ".join(missing))

    def _busy(self, on):
        for b in (self.id_btn, self.dump_btn):
            b.setEnabled(not on)

    def _start(self, fn):
        self._busy(True)
        worker = Worker(fn)
        worker.log.connect(self._log)
        worker.id_row.connect(lambda bank, ident: self.ids.appendPlainText(f"bank {bank}: {ident}"))
        worker.progress.connect(self.bar.setValue)
        worker.done.connect(self._finished)

        def thread_body():
            worker.run()

        self._worker = worker
        self.thread = threading.Thread(target=thread_body, daemon=True)
        self.thread.start()

    def _finished(self, ok, err):
        self._busy(False)
        if ok:
            self.bar.setValue(100)
            self._log("done")
        else:
            self._log(f"error: {err}")
            QMessageBox.warning(self, "NAND reader", err)

    def _read_id(self):
        cv = int(round(self.cv.value() * 100))
        banks = self.banks.value()
        pdir = self.payload_dir.text().strip()

        def job(w):
            for bank in range(1, banks + 1):
                ident = None
                for attempt in range(1, 31):
                    try:
                        nl.power_cycle(settle=nl.LONG_OFF_S)
                    except nl.ReaderError:
                        nl.bus_reset()
                    try:
                        with nl.Reader() as r:
                            r.load_payloads(pdir)
                            ident = r.read_id(bank)
                    except (nl.usb.core.USBError, nl.ReaderError) as exc:
                        w.log.emit(f"bank {bank} [{attempt}] no reply ({exc})")
                        continue
                    if ident[:4].hex() == "89c40832" or ident.count(0) != len(ident):
                        break
                    w.log.emit(f"bank {bank} [{attempt}] {ident[:4].hex(' ')}")
                if ident is None:
                    w.id_row.emit(bank, "no reply")
                else:
                    w.id_row.emit(bank, ident.hex(" "))

        self.ids.clear()
        self._start(job)

    def _dump(self):
        cv = int(round(self.cv.value() * 100))
        pdir = self.payload_dir.text().strip()
        out = self.out.text()
        start, end, unit, bank = (self.start.value(), self.end.value(),
                                  self.unit.value(), self.dbank.value())

        def job(w):
            w.log.emit("rail up")
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            written = 0
            pages = list(range(start, end))
            t0 = time.time()
            try:
                with open(out, "wb") as fh:
                    for n, page in enumerate(pages):
                        try:
                            nl.power_cycle(settle=nl.LONG_OFF_S)
                        except nl.ReaderError:
                            nl.bus_reset()
                        with nl.Reader() as r:
                            r.load_payloads(pdir)
                            r.boot_rail(cv)
                            d = r.read_page(page, unit=unit, bank=bank)
                        if d is None:
                            w.log.emit(f"p{page}: SHORT")
                            continue
                        fh.write(d)
                        written += len(d)
                        if d.count(0xFF) == len(d):
                            cls = "FF"
                        elif d.count(0x00) == len(d):
                            cls = "00"
                        else:
                            cls = "data"
                        w.progress.emit(int(100 * (n + 1) / len(pages)))
                        w.log.emit(f"p{page}: {cls} {len(d)} B  "
                                   f"{(written / 1e6 / max(time.time() - t0, 1e-9)):.2f} MB/s")
            finally:
                w.log.emit("rail down")
            w.log.emit(f"wrote {written} B to {out}")

        self.bar.setValue(0)
        self._start(job)


def main():
    app = QApplication(sys.argv)
    win = ReaderWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
