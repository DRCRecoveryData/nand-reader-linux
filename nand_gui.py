#!/usr/bin/env python3
"""PyQt6 front end that mirrors Soft-Center NAND Flash Reader v570 on Linux.

Layout follows the vendor window: Folder + Chip + Number on top, the
* / ID / Read / Conf / Extra buttons, and a Bank list with a Result column.

Vendor button map (recovered from NAND_Reader.exe v570 wide strings):
  *      chip auto-select (message sweep over the ID table)
  ID     Read ID per bank, matched against the built-in chip database
  Read   Page / Block / Blocks / Col / Row read with live progress
  Conf   Options (rail, timings, ID checks) and Config (raw design bytes)
  Extra  Test USB, Read ONFI, Read JEDEC, Chip Options,
         Read Many Times, Read Many Voltages

Every operation is read-only. No program / erase / write opcode is ever sent.
Long jobs run on a worker thread so the window stays live.
"""

import os
import sys
import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl
import payloads_check
from chip_db import CHIP_DB

WRITE_OPCODES = (0x80, 0x60, 0xD0, 0x10, 0x11, 0x15, 0xFF)


class Worker(QObject):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)
    id_row = pyqtSignal(int, str, str)          # bank, id, result
    progress = pyqtSignal(int)
    text = pyqtSignal(str)                       # bulk output (ID/ONFI/JEDEC)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    def run(self):
        try:
            self.fn(self)
            self.done.emit(True, "")
        except Exception as exc:  # surfaced in the UI, not swallowed
            self.done.emit(False, str(exc))


def _safe_cycle(settle=nl.LONG_OFF_S):
    try:
        nl.power_cycle(settle=settle)
    except nl.ReaderError:
        nl.bus_reset()


class ReaderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NAND Flash Reader v570 — Linux")
        self.resize(820, 700)
        self.thread = None
        self.worker = None
        self.cv = 330
        self.id_size = 4
        self.check_id = True
        self.after_reset = True
        self.read_bb = True
        self.timings_slow = False
        self.ignore_busy = False

        root = QWidget()
        layout = QVBoxLayout(root)

        srow = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet("font-size: 18px; color: #c62828;")
        self.dot.setToolTip("reader status")
        self.dot.mousePressEvent = lambda _e: self._refresh_status()
        srow.addWidget(self.dot)
        srow.addStretch(1)
        layout.addLayout(srow)
        self._refresh_status()

        # --- top strip: Folder + Chip + Number --------------------------------
        top = QFormLayout()
        self.folder = QLineEdit(os.getcwd())
        fb = QPushButton("…")
        fb.clicked.connect(self._pick_folder)
        frow = QHBoxLayout()
        frow.addWidget(self.folder)
        frow.addWidget(fb)
        self.chip = QComboBox()
        self.chip.addItem("(auto / all)", None)
        for ident in sorted(CHIP_DB):
            company, size = CHIP_DB[ident]
            self.chip.addItem(f"{ident}  {company}  {size}", ident)
        self.chip.setEditable(True)
        self.chip.setCurrentIndex(0)
        self.number = QSpinBox()
        self.number.setRange(1, 64)
        self.number.setValue(1)
        nrow = QHBoxLayout()
        nrow.addWidget(QLabel("×"))
        nrow.addWidget(self.number)
        top.addRow("Folder", frow)
        top.addRow("Chip", self.chip)
        top.addRow("Number", nrow)
        layout.addLayout(top)

        # --- action buttons ---------------------------------------------------
        actions = QHBoxLayout()
        star = QPushButton("*")
        star.setToolTip("Auto-select chip from the ID table")
        star.clicked.connect(self._auto_chip)
        ident_b = QPushButton("ID")
        ident_b.clicked.connect(self._read_id)
        read_b = QPushButton("Read")
        read_b.clicked.connect(self._read_dialog)
        conf_b = QPushButton("Conf")
        conf_b.clicked.connect(self._conf_menu)
        extra_b = QPushButton("Extra")
        extra_b.clicked.connect(self._extra_menu)
        self.actions = [star, ident_b, read_b, conf_b, extra_b]
        for b in self.actions:
            b.setMinimumHeight(30)
            actions.addWidget(b)
        layout.addLayout(actions)

        # --- bank table -------------------------------------------------------
        bank_box = QGroupBox("Banks")
        bl = QVBoxLayout(bank_box)
        self.bank_list = QTableWidget(0, 3)
        self.bank_list.setHorizontalHeaderLabels(["Bank", "Chip ID", "Result"])
        self.bank_list.verticalHeader().setVisible(False)
        self.bank_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.bank_list.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        hh = self.bank_list.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._fill_banks(self.chip.currentData())
        self.chip.currentIndexChanged.connect(
            lambda _i: self._fill_banks(self.chip.currentData()))
        bl.addWidget(self.bank_list)
        layout.addWidget(bank_box, 1)

        # --- log + progress ---------------------------------------------------
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        layout.addWidget(self.logv, 1)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        layout.addWidget(self.stop_btn)

        self.setCentralWidget(root)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(3000)

    # -- helpers -------------------------------------------------------------
    def _present(self):
        try:
            return bool(list(nl.usb.core.find(find_all=True,
                                              idVendor=nl.VID, idProduct=nl.PID)))
        except Exception:
            return False

    def _refresh_status(self):
        present = self._present()
        self.dot.setStyleSheet(
            "font-size: 18px; color: %s;" % ("#2e7d32" if present else "#c62828"))
        self.dot.setToolTip("reader present" if present else "reader not found")




    def _log(self, msg):
        self.logv.appendPlainText(msg)

    def _banks(self):
        data = self.chip.currentData()
        if data:
            company, size = CHIP_DB.get(data, ("", ""))
            n = 1
            if size:
                try:
                    n = int(size.split("x")[0].strip())
                except ValueError:
                    n = 1
            return max(1, min(4, n))
        return 1

    def _fill_banks(self, _data=None):
        self.bank_list.setRowCount(0)
        for bank in range(1, self._banks() + 1):
            r = self.bank_list.rowCount()
            self.bank_list.insertRow(r)
            self.bank_list.setItem(r, 0, QTableWidgetItem(f"Bank_{bank}"))
            self.bank_list.setItem(r, 1, QTableWidgetItem(""))
            self.bank_list.setItem(r, 2, QTableWidgetItem(""))

    def _set_row(self, bank, ident, result):
        for r in range(self.bank_list.rowCount()):
            cell = self.bank_list.item(r, 0)
            if cell and cell.text() == f"Bank_{bank}":
                self.bank_list.item(r, 1).setText(ident)
                self.bank_list.item(r, 2).setText(result)
                return
            if cell and cell.text() == f"Bank {bank}":
                self.bank_list.item(r, 1).setText(ident)
                self.bank_list.item(r, 2).setText(result)
                return


    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Select work folder",
                                             self.folder.text().strip())
        if d:
            self.folder.setText(d)

    def _payload_dir(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "payloads")

    # -- busy / lifecycle ----------------------------------------------------
    def _busy(self, on):
        for b in self.actions:
            b.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        if on:
            self._status_timer.stop()
        else:
            self._refresh_status()
            self._status_timer.start(3000)

    def _start(self, fn):
        self._busy(True)
        worker = Worker(fn)
        worker.log.connect(self._log)
        worker.text.connect(self._log)
        worker.id_row.connect(self._on_id_row)
        worker.progress.connect(self.bar.setValue)
        worker.done.connect(self._finished)
        self.worker = worker

        def body():
            worker.run()

        self.thread = threading.Thread(target=body, daemon=True)
        self.thread.start()

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self._log("stop requested — finishing current op")

    def _on_id_row(self, bank, ident, result):
        self._log(f"bank {bank}: {ident}  {result}")
        self._set_row(bank, ident, result)

    def _finished(self, ok, err):
        self._busy(False)
        if ok:
            self.bar.setValue(100)
            self._log("done")
        else:
            self._log(f"error: {err}")
            QMessageBox.warning(self, "NAND reader", err)

    # -- * auto chip ---------------------------------------------------------
    def _auto_chip(self):
        pdir = self._payload_dir()
        self._fill_banks()

        def job(w):
            ident = None
            for attempt in range(1, 31):
                if w.stopped.is_set():
                    return
                _safe_cycle()
                try:
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        ident = r.read_id(1)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"[{attempt}] no reply ({exc})")
                    continue
                if ident[:4].hex() == "89c40832" or ident.count(0) != len(ident):
                    break
                w.log.emit(f"[{attempt}] {ident[:4].hex(' ')}")
            if ident is None:
                w.id_row.emit(1, "no reply", "Fail")
                return
            key = " ".join(f"{b:02x}" for b in ident[:4])
            company, size = CHIP_DB.get(key, ("Unknown", "?"))
            w.id_row.emit(1, ident.hex(" "), f"{company} {size}")

        self._start(job)

    # -- ID ------------------------------------------------------------------
    def _read_id(self):
        pdir = self._payload_dir()
        banks = self._banks()
        self._fill_banks()

        def job(w):
            for bank in range(1, banks + 1):
                if w.stopped.is_set():
                    return
                ident = None
                for attempt in range(1, 31):
                    if w.stopped.is_set():
                        return
                    _safe_cycle()
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
                    w.id_row.emit(bank, "no reply", "Fail")
                else:
                    key = " ".join(f"{b:02x}" for b in ident[:4])
                    company, size = CHIP_DB.get(key, ("Unknown", "?"))
                    w.id_row.emit(bank, ident.hex(" "), f"{company} {size}")

        self._start(job)

    # -- Read dialog ---------------------------------------------------------
    def _read_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Read")
        form = QFormLayout(dlg)
        page = QSpinBox(); page.setRange(0, 0x7FFFFFFF)
        count = QSpinBox(); count.setRange(1, 2048); count.setValue(8)
        block = QSpinBox(); block.setRange(-1, 0x3FFFFF)
        block.setSpecialValueText("(off)")
        blocks = QSpinBox(); blocks.setRange(1, 256); blocks.setValue(1)
        unit = QSpinBox(); unit.setRange(1, 65535); unit.setValue(2048)
        bank = QSpinBox(); bank.setRange(1, 4)
        form.addRow("Page", page)
        form.addRow("Count", count)
        form.addRow("Block (overrides page)", block)
        form.addRow("Blocks", blocks)
        form.addRow("Unit (bytes)", unit)
        form.addRow("Bank", bank)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        pdir = self._payload_dir()
        pw = page.value()
        bw = block.value()
        nb = blocks.value()
        unit_v = unit.value()
        bank_v = bank.value()
        if bw >= 0:
            pages = list(range(bw * 8, bw * 8 + 8 * nb))
        else:
            pages = list(range(pw, pw + count.value()))
        # A bank dies to 30-fill if left unpowered; always boot the rail first.
        self.bar.setValue(0)

        def job(w):
            t0 = time.time()
            ok = 0
            for n, pg in enumerate(pages):
                if w.stopped.is_set():
                    return
                _safe_cycle()
                try:
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        r.boot_rail(self.cv)
                        d = r.read_page(pg, unit=unit_v, bank=bank_v)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"p{pg}: error ({exc})")
                    d = None
                if d is None:
                    w.log.emit(f"p{pg}: SHORT")
                else:
                    ok += 1
                    if d.count(0xFF) == len(d):
                        cls = "FF"
                    elif d.count(0x00) == len(d):
                        cls = "00"
                    else:
                        cls = "data"
                    w.log.emit(f"p{pg}: {cls} {len(d)} B")
                w.progress.emit(int(100 * (n + 1) / len(pages)))
            w.log.emit(f"read {ok}/{len(pages)} page(s) in {time.time() - t0:.1f}s")

        self._start(job)

    # -- Conf menu -----------------------------------------------------------
    def _conf_menu(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Conf")
        v = QVBoxLayout(dlg)
        opt = QPushButton("Options")
        opt.clicked.connect(lambda: (dlg.accept(), self._options_dialog()))
        cfg = QPushButton("Config")
        cfg.clicked.connect(lambda: (dlg.accept(), self._config_dialog()))
        v.addWidget(opt)
        v.addWidget(cfg)
        dlg.exec()

    def _options_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Options")
        form = QFormLayout(dlg)
        rail = QDoubleSpinBox(); rail.setRange(1.2, 3.3); rail.setSingleStep(0.05)
        rail.setDecimals(2); rail.setValue(self.cv / 100)
        timings = QComboBox(); timings.addItems(["Slow", "Fast"])
        timings.setCurrentText("Slow" if self.timings_slow else "Fast")
        ignore_busy = QCheckBox("Ignore Busy"); ignore_busy.setChecked(self.ignore_busy)
        id_size = QSpinBox(); id_size.setRange(2, 8); id_size.setValue(self.id_size)
        check_id = QCheckBox("Check ID"); check_id.setChecked(self.check_id)
        after_reset = QCheckBox("After Reset"); after_reset.setChecked(self.after_reset)
        before_read = QCheckBox("Before Read"); before_read.setChecked(True)
        read_bb = QCheckBox("Read bb"); read_bb.setChecked(self.read_bb)
        pdir = QLineEdit(self._payload_dir())
        pb = QPushButton("Check")
        def check():
            rows, missing = payloads_check.check(pdir.text().strip())
            for name, note, size, sha, swap in rows:
                self._log(f"{name:<18}{note}")
            self._log("all payloads present" if not missing
                      else "missing: " + ", ".join(missing))
        pb.clicked.connect(check)
        prow = QHBoxLayout(); prow.addWidget(pdir); prow.addWidget(pb)
        form.addRow("Power Adapter", QLabel("Voltage"))
        form.addRow("Rail (V)", rail)
        form.addRow("Timings", timings)
        form.addRow("", ignore_busy)
        form.addRow("ID Size", id_size)
        form.addRow("", check_id)
        form.addRow("", after_reset)
        form.addRow("", before_read)
        form.addRow("", read_bb)
        form.addRow("Payloads", prow)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cv = int(round(rail.value() * 100))
            self.timings_slow = timings.currentText() == "Slow"
            self.ignore_busy = ignore_busy.isChecked()
            self.id_size = id_size.value()
            self.check_id = check_id.isChecked()
            self.after_reset = after_reset.isChecked()
            self.read_bb = read_bb.isChecked()
            self._log(f"options: rail {self.cv/100:.2f} V, id_size {self.id_size}")

    def _config_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Config")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Design payloads (read-only):"))
        out = QPlainTextEdit(); out.setReadOnly(True)
        out.setPlainText(
            f"nand_init.bin : chip design image\n"
            f"power_init.bin: power design image\n\n"
            f"VID:PID  {nl.VID:04x}:{nl.PID:04x}\n"
            f"EP_OUT   0x{nl.EP_OUT:02x}\n"
            f"EP_IN    0x{nl.EP_IN:02x}\n"
            f"IFACE    {nl.IFACE}\n"
            f"read cols {nl.READ_COL_CYCLES}  rows {nl.READ_ROW_CYCLES}\n"
            f"opcodes forwarded by FPGA: 0x90 ID, 0x30 READ only\n")
        v.addWidget(out)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()

    # -- Extra menu ----------------------------------------------------------
    def _extra_menu(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Extra")
        v = QVBoxLayout(dlg)
        buttons = [
            ("Test USB", self._test_usb),
            ("Read ONFI", lambda: self._read_parameter_page("ONFI")),
            ("Read JEDEC", lambda: self._read_parameter_page("JEDEC")),
            ("Chip Options", self._config_dialog),
            ("Read Many Times", self._read_many_times),
            ("Read Many Voltages", self._read_many_voltages),
        ]
        for label, fn in buttons:
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, f=fn, d=dlg: (d.accept(), f()))
            v.addWidget(b)
        dlg.exec()

    def _test_usb(self):
        def job(w):
            _safe_cycle()
            with nl.Reader() as r:
                w.log.emit(f"device: {r.dev.manufacturer} / {r.dev.product}")
                w.log.emit(f"  {r.dev.idVendor:04x}:{r.dev.idProduct:04x} "
                           f"cfg {r.dev.get_active_configuration().bConfigurationValue}")
                board = r.boot_power(self.cv)
                w.log.emit(f"power echo: {bytes(board).hex(' ')}")
            w.log.emit("Test USB: OK")
        self._start(job)

    def _read_parameter_page(self, kind):
        # ONFI (0x90 addr 0x20) and JEDEC (0x90 addr 0x40) are READ-ID variants;
        # the FPGA forwards only the 4-byte ID read, so these come back as the
        # same ID or a constant. Report honestly rather than inventing a table.
        self._log(f"{kind}: this reader only forwards the 4-byte ID read; the "
                  f"{kind} parameter page is not reachable through the FPGA.")

    def _read_many_times(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Read Many Times")
        form = QFormLayout(dlg)
        number = QSpinBox(); number.setRange(2, 64); number.setValue(16)
        page = QSpinBox(); page.setRange(0, 0x7FFFFFFF)
        bank = QSpinBox(); bank.setRange(1, 4)
        unit = QSpinBox(); unit.setRange(1, 65535); unit.setValue(2048)
        form.addRow("Number", number)
        form.addRow("Page", page)
        form.addRow("Bank", bank)
        form.addRow("Unit", unit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        pdir = self._payload_dir(); n = number.value(); pg = page.value()
        bn = bank.value(); u = unit.value()

        def job(w):
            reads = []
            for i in range(n):
                if w.stopped.is_set():
                    return
                _safe_cycle()
                try:
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        r.boot_rail(self.cv)
                        d = r.read_page(pg, unit=u, bank=bn)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"[{i}] error ({exc})"); continue
                if d is None:
                    w.log.emit(f"[{i}] SHORT"); continue
                reads.append(d)
                w.progress.emit(int(100 * (i + 1) / n))
            if not reads:
                w.log.emit("no successful reads")
                return
            voted, marginal = nl.majority_vote(reads)
            diffs = sum(1 for i in range(len(reads[0]))
                        if any(rr[i] != reads[0][i] for rr in reads)) if len(reads[0]) else 0
            w.log.emit(f"{n} reads, {len(reads)} ok, {marginal} marginal bits, "
                       f"{diffs} bytes differ across reads")
            w.text.emit(f"voted page head: {voted[:32].hex(' ')}")
        self._start(job)

    def _read_many_voltages(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Read Many Voltages")
        form = QFormLayout(dlg)
        frm = QDoubleSpinBox(); frm.setRange(1.2, 3.3); frm.setSingleStep(0.05)
        frm.setDecimals(2); frm.setValue(3.3)
        to = QDoubleSpinBox(); to.setRange(1.2, 3.3); to.setSingleStep(0.05)
        to.setDecimals(2); to.setValue(1.8)
        step = QDoubleSpinBox(); step.setRange(0.05, 0.5); step.setSingleStep(0.05)
        step.setDecimals(2); step.setValue(0.05)
        page = QSpinBox(); page.setRange(0, 0x7FFFFFFF)
        bank = QSpinBox(); bank.setRange(1, 4)
        unit = QSpinBox(); unit.setRange(1, 65535); unit.setValue(2048)
        form.addRow("From (V)", frm)
        form.addRow("To (V)", to)
        form.addRow("Step", step)
        form.addRow("Page", page)
        form.addRow("Bank", bank)
        form.addRow("Unit", unit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if step.value() <= 0:
            QMessageBox.warning(self, "Read Many Voltages", "Step = 0")
            return
        pdir = self._payload_dir()
        lo, hi = min(frm.value(), to.value()), max(frm.value(), to.value())
        steps = []
        v = lo
        while v <= hi + 1e-9:
            steps.append(int(round(v * 100)))
            v += step.value()
        pg, bn, u = page.value(), bank.value(), unit.value()

        def job(w):
            for i, cv in enumerate(steps):
                if w.stopped.is_set():
                    return
                _safe_cycle()
                try:
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        r.boot_rail(cv)
                        d = r.read_page(pg, unit=u, bank=bn)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"{cv/100:.2f} V: error ({exc})"); continue
                if d is None:
                    w.log.emit(f"{cv/100:.2f} V: SHORT"); continue
                ones = sum(bin(b).count("1") for b in d)
                w.log.emit(f"{cv/100:.2f} V: {len(d)} B, {ones} bits set, "
                           f"head {d[:8].hex(' ')}")
                w.progress.emit(int(100 * (i + 1) / len(steps)))
        self._start(job)


def main():
    app = QApplication(sys.argv)
    win = ReaderWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
