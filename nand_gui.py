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
        d = QFileDialog.getExistingDirectory(self, "Payload directory", self.payload_dir.text())
        if d:
            self.payload_dir.setText(d)

    def _pick_out(self):
        p, _ = QFileDialog.getSaveFileName(self, "Dump to", self.out.text())
        if p:
            self.out.setText(p)

    def _check_payloads(self):
        rows, missing = payloads_check.check(self.payload_dir.text())
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
        pdir = self.payload_dir.text()

        def job(w):
            with nl.Reader() as r:
                r.load_payloads(pdir)
                time.sleep(nl.SETTLE_S)
                w.log.emit(f"rail up: {r.power(cv)} cV")
                try:
                    for bank in range(1, banks + 1):
                        w.id_row.emit(bank, r.read_id(bank).hex(" "))
                finally:
                    r.power_down()
                    w.log.emit("rail down")

        self.ids.clear()
        self._start(job)

    def _dump(self):
        cv = int(round(self.cv.value() * 100))
        pdir = self.payload_dir.text()
        out = self.out.text()
        start, end, unit, bank = (self.start.value(), self.end.value(),
                                  self.unit.value(), self.dbank.value())
        total = (end - start) * unit

        def job(w):
            with nl.Reader() as r:
                r.load_payloads(pdir)
                time.sleep(nl.SETTLE_S)
                w.log.emit(f"rail up: {r.power(cv)} cV")
                t0 = time.time()
                written = 0
                try:
                    r._chunked(r.chip_image)
                    r._out(r.program_block)
                    ctl = bytearray(32)
                    ctl[0:4] = start.to_bytes(4, "little")
                    ctl[4:8] = end.to_bytes(4, "little")
                    ctl[8:10] = unit.to_bytes(2, "little")
                    ctl[11] = 1 << (bank - 1)
                    ctl[14] = 0x35
                    ctl[15] = 0x22
                    ctl[16:] = b"\xff" * 16
                    r._out(bytes(ctl))
                    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
                    with open(out, "wb") as fh:
                        while written < total:
                            want = min(1 << 20, total - written)
                            buf = r._in(want)
                            if not buf:
                                raise nl.ReaderError(f"short delivery at {written} B")
                            if len(buf) > want:
                                buf = buf[:want]
                            fh.write(buf)
                            written += len(buf)
                            w.progress.emit(int(100 * written / total))
                            w.log.emit(f"{written}/{total} B  "
                                       f"{written / 1e6 / max(time.time() - t0, 1e-9):.1f} MB/s")
                finally:
                    r.power_down()
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
