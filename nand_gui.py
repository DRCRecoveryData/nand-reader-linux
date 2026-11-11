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

from PyQt6.QtCore import QObject, QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
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


KNOWN_FILL = {
    0x00: "bus returns all zero",
    0xFF: "bus floats at the pull-ups",
    0x27: "rail present but no line is being driven",
    0x30: "DMA trailer echo: this bank set is not selected",
}


def classify_id(reply, rail_cv=None, want_cv=None):
    """Name what a READ ID reply means, without knowing any pinout.

    Only the wire behaviour is used: a real die returns four vendor-derived
    bytes; a mis-wired or unselected socket returns fill. `rail_cv` far from
    `want_cv` means the setpoint never reached the die.
    """
    if rail_cv is not None and want_cv is not None and abs(rail_cv - want_cv) > 25:
        return "starved", f"rail read {rail_cv} cV for a {want_cv} cV setpoint"
    if not reply:
        return "no-reply", "nothing came back inside the listen window"
    if len(set(reply)) == 1:
        note = KNOWN_FILL.get(reply[0], "constant fill")
        return "fill", f"{reply.hex(' ')}: {note}"
    period = None
    for p in range(1, max(1, len(reply) // 2) + 1):
        if reply == reply[:p] * (len(reply) // p):
            period = p
            break
    if period is not None and period <= 2:
        return "pattern", f"{reply.hex(' ')}: repeats every {period} byte(s)"
    key = " ".join(f"{b:02x}" for b in reply[:4])
    if key in CHIP_DB:
        return "die", f"{key}: {CHIP_DB[key][0]} {CHIP_DB[key][1]}"
    # Every vendor in the database uses a fixed set of second bytes; a reply
    # whose first two bytes fall outside all of them is a shifted bus, not a
    # part the database happens to lack.
    pairs = {}
    for k in CHIP_DB:
        b = bytes.fromhex(k.replace(" ", ""))
        pairs.setdefault(b[0], set()).add(b[1])
    if reply[0] in pairs and reply[1] in pairs[reply[0]]:
        return "unknown-id", f"{reply.hex(' ')}: vendor 0x{reply[0]:02x} answers"
    return "garbled", f"{reply.hex(' ')}: first bytes match no vendor"


def pin_hint(kind, reply):
    """Translate a failure class into the pin class most likely mis-wired."""
    if kind == "die":
        return "ID lines, CE and the control set are all correct"
    if kind == "fill":
        b = reply[0] if reply else None
        if b == 0x27:
            return "VCC is not reaching the die: check the VCC balls and GND"
        if b == 0x00:
            return "CE/RB not driven: CE (chip enable) or RB is on the wrong ball"
        if b == 0xFF:
            return "nothing is touching the die: data lines or GND are open"
        if b == 0x30:
            return "bank select: this set is not wired, or another bank is"
    if kind == "starved":
        return "VCC is on a series or signal ball: the die sees far less than set"
    if kind == "pattern":
        return "the data bus is offset or shorted: I/O lines swapped or tied"
    if kind == "garbled":
        return "the address/command lines are partly wrong: vendor byte shifted"
    if kind == "unknown-id":
        return "a real die answered but not in the database: pinout is correct"
    return "no power path: VCC/GND wrong, or the die is dead"


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
            company, size, cap, bus, _cs = CHIP_DB[ident]
            self.chip.addItem(f"{ident}  {company}  {size}  {cap} {bus}-bit", ident)
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
        self.logv = QTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setFont(QFont("Monospace", 10))
        self.logv.setStyleSheet("font-family: monospace;")
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




    @staticmethod
    def _log_color(msg):
        m = msg.lower()
        if msg.startswith(("  ", "distinct replies")):
            return "#37474f"        # report body, judged on its own line above
        if any(k in m for k in ("error", "fail", "no reply", "timeout",
                                "no chip", "not found", "short", "wrong",
                                "bad", "starved")):
            return "#c62828"        # red
        if m.endswith(("  id", "  other", "  fill")):
            return "#2e7d32" if m.endswith("  id") else "#c62828"
        if any(k in m for k in ("unknown", "warning", "[!]", "(empty)")):
            return "#ef6c00"        # orange
        if any(k in m for k in ("ok", "done", "intel", "micron", "samsung",
                                "sandisk", "hynix", "toshiba", "spectek",
                                "bank 1:", "bank 2:", "power echo")):
            return "#2e7d32"        # green
        return "#37474f"            # plain slate

    def _log(self, msg):
        import html
        from PyQt6.QtGui import QTextCursor
        cur = self.logv.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        for line in str(msg).splitlines() or [""]:
            span = (f'<span style="color:{self._log_color(line)}; '
                    f'white-space:pre">{html.escape(line)}</span><br>')
            cur.insertHtml(span)
        self.logv.setTextCursor(cur)
        self.logv.ensureCursorVisible()

    def _banks(self):
        data = self.chip.currentData()
        if data:
            company, size = CHIP_DB.get(data, ("", ""))[:2]
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

    def _fill_all_banks(self):
        self.bank_list.setRowCount(0)
        for bank in range(1, 5):
            r = self.bank_list.rowCount()
            self.bank_list.insertRow(r)
            self.bank_list.setItem(r, 0, QTableWidgetItem(f"Bank_{bank}"))
            self.bank_list.setItem(r, 1, QTableWidgetItem(""))
            self.bank_list.setItem(r, 2, QTableWidgetItem(""))


    def _result_color(self, ident, result):
        if result in ("no chip", "Fail", "--") or "no reply" in result:
            return "#c62828"       # red: nothing usable
        if result.startswith("Unknown"):
            return "#ef6c00"       # orange: ID read but not in the database
        return "#2e7d32"           # green: matched a known chip

    def _set_row(self, bank, ident, result):
        color = QColor(self._result_color(ident, result))
        for r in range(self.bank_list.rowCount()):
            cell = self.bank_list.item(r, 0)
            if cell and cell.text() in (f"Bank_{bank}", f"Bank {bank}"):
                idc = self.bank_list.item(r, 1)
                resc = self.bank_list.item(r, 2)
                idc.setText(ident)
                resc.setText(result)
                idc.setForeground(color)
                resc.setForeground(color)
                f = resc.font(); f.setBold(True); resc.setFont(f)
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
                        ident = r.read_id(1, id_size=self.id_size)
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
            company, size = CHIP_DB.get(key, ("Unknown", "?"))[:2]
            w.id_row.emit(1, ident[:8].hex(" "), f"{company} {size}")

        self._start(job)

    # -- ID ------------------------------------------------------------------
    def _read_id(self):
        pdir = self._payload_dir()
        banks = 4
        self._fill_all_banks()
        self.bar.setValue(0)

        def job(w):
            for i, bank in enumerate(range(1, banks + 1)):
                if w.stopped.is_set():
                    return
                ident = None
                good = None
                blank = 0
                # One boot per bank is a hard limit, so a bank that answers
                # all-0/all-FF gets one confirming retry, never a long storm.
                for attempt in range(1, 4):
                    if w.stopped.is_set():
                        return
                    settle = 0.5 if attempt <= 2 else nl.LONG_OFF_S
                    try:
                        nl.power_cycle(settle=settle)
                    except nl.ReaderError:
                        nl.bus_reset()
                    try:
                        with nl.Reader() as r:
                            r.load_payloads(pdir)
                            ident = r.read_id(bank, listen_ms=1500,
                                              id_size=self.id_size)
                    except (nl.usb.core.USBError, nl.ReaderError) as exc:
                        w.log.emit(f"bank {bank} [{attempt}] no reply ({exc})")
                        continue
                    head = ident[:4]
                    if head.hex() == "89c40832" or ident.count(0) != len(ident):
                        good = ident
                        break
                    if head.hex() in ("00000000", "ffffffff"):
                        blank += 1
                        if blank >= 2:
                            break
                    w.log.emit(f"bank {bank} [{attempt}] {head.hex(' ')}")
                w.progress.emit(int(100 * (i + 1) / banks))
                if good is not None:
                    key = " ".join(f"{b:02x}" for b in good[:4])
                    company, size = CHIP_DB.get(key, ("Unknown", "?"))[:2]
                    w.id_row.emit(bank, good.hex(" "),
                                  f"{company} {size}" if key in CHIP_DB else "Unknown")
                else:
                    w.id_row.emit(bank, "--", "no chip")

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
            ("Rail Research", self._rail_research),
            ("Socket Check", self._socket_check),
            ("Rail-Only Bring-Up (guarded)", self._rail_only_bringup),
            ("Live Socket Monitor", self._live_monitor),
        ]
        for label, fn in buttons:
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, f=fn, d=dlg: (d.accept(), f()))
            v.addWidget(b)
        dlg.exec()

    def _test_usb(self):
        pdir = self._payload_dir()

        def job(w):
            _safe_cycle()
            with nl.Reader() as r:
                r.load_payloads(pdir)
                w.log.emit(f"device: {r.dev.manufacturer} / {r.dev.product}")
                w.log.emit(f"  {r.dev.idVendor:04x}:{r.dev.idProduct:04x} "
                           f"cfg {r.dev.get_active_configuration().bConfigurationValue}")
                try:
                    board = r.boot_power(self.cv)
                except nl.usb.core.USBError as exc:
                    board = b""
                    w.log.emit(f"power echo: no reply ({exc})")
                if board:
                    w.log.emit(f"power echo: {bytes(board).hex(' ')}")
                else:
                    w.log.emit("power echo: (empty)")
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
        frm = QDoubleSpinBox(); frm.setRange(nl.CV_MIN / 100, nl.CV_MAX / 100)
        frm.setSingleStep(0.05)
        frm.setDecimals(2); frm.setValue(3.3)
        to = QDoubleSpinBox(); to.setRange(nl.CV_MIN / 100, nl.CV_MAX / 100)
        to.setSingleStep(0.05)
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
        start, end = frm.value(), to.value()
        sp = step.value() if end >= start else -step.value()
        n = int(round(abs(end - start) / step.value()))
        steps = [int(round((start + i * sp) * 100)) for i in range(n + 1)]
        if steps[-1] != int(round(end * 100)):  # always land exactly on To
            steps.append(int(round(end * 100)))
        pg, bn, u = page.value(), bank.value(), unit.value()

        def job(w):
            reads = []
            for i, cv in enumerate(steps):
                if w.stopped.is_set():
                    return
                try:
                    _safe_cycle(settle=0.5)
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        d = r.page_at(pg, unit=u, bank=bn, cv=cv)
                        rail = r.rail_cv
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"{cv/100:.2f} V: error ({exc})")
                    w.progress.emit(int(100 * (i + 1) / len(steps)))
                    continue
                if d is None:
                    w.log.emit(f"{cv/100:.2f} V: SHORT")
                    w.progress.emit(int(100 * (i + 1) / len(steps)))
                    continue
                reads.append((cv, d, rail))
                w.progress.emit(int(100 * (i + 1) / len(steps)))
            # The reference is the first read that is not uniform fill: a sweep
            # that starts below the chip's working rail begins with ff/00/27
            # fill, and that fill must not become the yardstick.
            ref = next((d for _cv, d, _r in reads
                        if sum(bin(b).count("1") for b in d) not in (0, len(d) * 8)),
                       None)
            if ref is not None:
                w.log.emit("reference: first non-fill read; "
                           "OK = matches it, BAD = fill or large mismatch")
            first_ok = last_ok = None
            for cv, d, rail in reads:
                ones = sum(bin(b).count("1") for b in d)
                full = len(d) * 8
                if ones in (0, full):
                    verdict = f"BAD {ones * 100 // full}% ones"
                elif ref is None:
                    verdict = "no clean read"
                else:
                    diff = sum(1 for x, y in zip(ref, d) if x != y)
                    if diff == 0:
                        verdict = "OK (same as ref)"
                    elif diff <= len(d) // 100:
                        verdict = f"OK ({diff} B differ)"
                    else:
                        verdict = f"BAD ({diff} B differ)"
                if verdict.startswith("OK"):
                    first_ok = cv if first_ok is None else first_ok
                    last_ok = cv
                meas = f"{rail/100:.2f} V" if rail else "?"
                w.log.emit(f"{cv/100:.2f} V (rail {meas}): {len(d)} B, "
                           f"{ones} bits set, head {d[:8].hex(' ')}  {verdict}")
            lo, hi = min(steps), max(steps)
            if first_ok is None:
                w.text.emit(f"working range: none in {lo/100:.2f}-{hi/100:.2f} V")
            else:
                w.text.emit(f"working range: {first_ok/100:.2f}-{last_ok/100:.2f} V "
                            f"(swept {lo/100:.2f}-{hi/100:.2f} V, "
                            f"{len(steps)} steps)")
        self._start(job)

    def _rail_research(self):
        """Map the rail band in which the die answers, for an unknown-pinout part.

        This is the only pinout datum this board can produce: VCC/GND must
        already be wired correctly, because the FPGA drives every other line
        as a NAND pin and offers no continuity or per-pin measurement. Each
        step is one cold boot, so the range is capped to keep the run short.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Rail Research")
        form = QFormLayout(dlg)
        lo = QDoubleSpinBox(); lo.setRange(nl.CV_MIN / 100, nl.CV_MAX / 100)
        lo.setDecimals(2); lo.setSingleStep(0.05); lo.setValue(1.80)
        hi = QDoubleSpinBox(); hi.setRange(nl.CV_MIN / 100, nl.CV_MAX / 100)
        hi.setDecimals(2); hi.setSingleStep(0.05); hi.setValue(3.30)
        step = QDoubleSpinBox(); step.setRange(0.05, 0.5)
        step.setDecimals(2); step.setSingleStep(0.05); step.setValue(0.10)
        bank = QSpinBox(); bank.setRange(1, 4)
        ident = QLineEdit("89 c4 08 32")
        ident.setPlaceholderText("expected ID (blank = match the first real one)")
        form.addRow("From (V)", lo)
        form.addRow("To (V)", hi)
        form.addRow("Step", step)
        form.addRow("Bank", bank)
        form.addRow("Expected ID", ident)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        pdir = self._payload_dir()
        start, end = lo.value(), hi.value()
        sp = step.value() if end >= start else -step.value()
        n = int(round(abs(end - start) / step.value()))
        steps = [int(round((start + i * sp) * 100)) for i in range(n + 1)]
        if steps[-1] != int(round(end * 100)):
            steps.append(int(round(end * 100)))
        bn = bank.value()
        want = ident.text().replace(" ", "").lower()

        def job(w):
            w.log.emit(f"rail research: {len(steps)} boots, bank {bn}, "
                       f"safe DAC band {nl.CV_MIN/100:.2f}-{nl.CV_MAX/100:.2f} V")
            w.log.emit("VCC/GND must already be correct: every other line is "
                       "driven as a NAND pin, this board cannot measure them")
            rows = []
            for i, cv in enumerate(steps):
                if w.stopped.is_set():
                    return
                try:
                    _safe_cycle(settle=0.5)
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        got, rail_cv = r.id_at(bn, cv=cv, id_size=self.id_size)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"{cv/100:.2f} V: error ({exc})")
                    w.progress.emit(int(100 * (i + 1) / len(steps)))
                    continue
                head = got.hex(" ").replace(" ", "")[:8]
                fill = head in ("", "00000000", "ffffffff", "27272727", "30303030")
                if want:
                    kind = "ID" if head == want else ("fill" if fill else "other")
                else:
                    kind = "fill" if fill else "ID"
                # One boot sometimes samples the rail before it has settled;
                # a reading far from the setpoint is that, not the real rail.
                rail_ok = rail_cv is not None and abs(rail_cv - cv) <= 10
                rows.append((cv, rail_cv, head, kind))
                meas = f"{rail_cv/100:.2f} V" if rail_cv else "?"
                note = "" if rail_ok else "  (rail unsettled, retry to confirm)"
                w.log.emit(f"{cv/100:.2f} V (rail {meas}): {got.hex(' ')}  "
                           f"{kind}{note}")
                w.progress.emit(int(100 * (i + 1) / len(steps)))

            good = [cv for cv, _rc, _h, k in rows if k == "ID"]
            w.log.emit("")
            if good:
                w.log.emit(f"expected ID answered over "
                           f"{min(good)/100:.2f}-{max(good)/100:.2f} V "
                           f"({len(good)} of {len(steps)} steps)")
            else:
                w.log.emit("no step returned the expected ID")
            bands = {}
            for cv, _rc, h, _k in rows:
                bands.setdefault(h, []).append(cv)
            w.log.emit("distinct replies:")
            for head, cvs in sorted(bands.items(), key=lambda kv: -len(kv[1])):
                pretty = " ".join(head[j:j + 2] for j in range(0, 8, 2)) or "(none)"
                first = int(head[:2], 16) if head else 0
                vendors = {v[0] for k, v in CHIP_DB.items()
                           if int(k.split()[0], 16) == first
                           and v[0] not in ("", "Unknown")}
                known = ", ".join(sorted(vendors)) or "no vendor for this prefix"
                size = CHIP_DB.get(pretty, ("", ""))[1]
                span = f"{min(cvs)/100:.2f}-{max(cvs)/100:.2f} V ({len(cvs)}x)"
                w.log.emit(f"  {pretty:<12} {span:<18} "
                           f"{known}{' ' + size if size else ''}")
            if want and len(bands) > 1:
                w.log.emit("more than one reply: only the band that matches the "
                           "expected ID is the die reading correctly; the others "
                           "are the die starved or the bus not driven")
            w.log.emit("")
            w.log.emit("before powering an unknown part, measure with a DMM:")
            w.log.emit("  - VCC to GND: no reading below ~100 ohm (dead short)")
            w.log.emit("  - each candidate pin to GND and to VCC in diode mode")
            w.log.emit("  - only pins with a diode drop belong on the I/O or "
                       "control lines, grounds read 0 ohm to GND")
        self._start(job)

    def _socket_check(self):
        """Validate a soldered socket blind: no pinout is entered, only the
        reply signature at several rails and banks is judged."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Socket Check")
        form = QFormLayout(dlg)
        rails = QLineEdit("1.80, 2.50, 3.30")
        rails.setPlaceholderText("rail setpoints in volts, comma separated")
        banks = QLineEdit("1, 2, 3, 4")
        banks.setPlaceholderText("banks to probe")
        form.addRow("Rails (V)", rails)
        form.addRow("Banks", banks)
        note = QLabel("Nothing about the pinout is entered: the result comes "
                      "only from what the die answers. Each rail x bank is one "
                      "cold boot (~3 s).")
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            cvs = [int(round(float(v) * 100)) for v in rails.text().split(",") if v.strip()]
            bns = [int(v) for v in banks.text().split(",") if v.strip()]
        except ValueError:
            QMessageBox.warning(self, "Socket Check", "use numbers like 3.30")
            return
        cvs = [c for c in cvs if nl.CV_MIN <= c <= nl.CV_MAX]
        if not cvs or not bns:
            QMessageBox.warning(self, "Socket Check", "no valid rail or bank")
            return
        pdir = self._payload_dir()
        runs = [(cv, bn) for cv in cvs for bn in bns]

        def job(w):
            w.log.emit(f"socket check: {len(runs)} boots, no pinout assumed")
            ok = 0
            hints = []
            for i, (cv, bn) in enumerate(runs):
                if w.stopped.is_set():
                    return
                try:
                    _safe_cycle(settle=0.5)
                    with nl.Reader() as r:
                        r.load_payloads(pdir)
                        got, rail = r.id_at(bn, cv=cv, id_size=self.id_size)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    w.log.emit(f"{cv/100:.2f} V bank {bn}: no-reply ({exc})")
                    w.progress.emit(int(100 * (i + 1) / len(runs)))
                    continue
                kind, detail = classify_id(got, rail, cv)
                if kind == "die":
                    ok += 1
                else:
                    hints.append(pin_hint(kind, got))
                w.log.emit(f"{cv/100:.2f} V bank {bn}: {kind} - {detail}")
                w.progress.emit(int(100 * (i + 1) / len(runs)))
            w.log.emit("")
            if ok:
                w.log.emit(f"PASS: a die answered on {ok} of {len(runs)} boots")
                w.log.emit("pin hint: " + pin_hint("die", b""))
            else:
                w.log.emit("FAIL: no boot returned a die ID")
            for h in sorted(set(hints)):
                w.log.emit("pin hint: " + h)
        self._start(job)

    def _rail_only_bringup(self):
        """Power VCC/GND and stop. The chip design is never loaded, so the
        FPGA never drives NAND control/data lines into unknown balls."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Rail-Only Bring-Up (guarded)")
        form = QFormLayout(dlg)
        rail = QDoubleSpinBox()
        rail.setRange(nl.CV_MIN / 100, nl.CV_MAX / 100)
        rail.setDecimals(2); rail.setSingleStep(0.05); rail.setValue(self.cv / 100)
        rail.setSuffix(" V")
        form.addRow("Rail setpoint", rail)
        note = QLabel("Loads ONLY the power design: the rail is set and the board "
                      "is left idle. The chip design is never loaded, so no NAND "
                      "control or data line is driven into an unknown ball.\n\n"
                      "This is the safe state for an all-unknown-pin part. The "
                      "reader cannot measure current, so it cannot prove a ball is "
                      "not shorted to VCC/GND - use a current-limited supply or "
                      "series resistors if the pinout is not verified.")
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cv = int(round(rail.value() * 100))
        pdir = self._payload_dir()

        def job(w):
            w.log.emit(f"rail-only bring-up at {cv/100:.2f} V (no chip design)")
            try:
                _safe_cycle(settle=0.5)
                with nl.Reader() as r:
                    r.load_payloads(pdir)
                    r.drain_in()
                    r._chunked(r.power_image)
                    r._out(nl.cv_to_dac(cv))
                    e = bytes(r.dev.read(nl.EP_IN, 8, nl.IN_TIMEOUT_MS))
                    got = nl.decode_cv(e) if len(e) >= 4 else None
                    traffic = r.drain_in(timeout_ms=300)
            except (nl.usb.core.USBError, nl.ReaderError) as exc:
                w.log.emit(f"rail bring-up failed: {exc}")
                return
            if got is None:
                w.log.emit("power design: no rail readback")
            else:
                w.log.emit(f"power design: rail {got} cV for a {cv} cV setpoint")
            w.log.emit(f"idle socket traffic: {len(traffic)} bytes "
                       "(0 = no line driven)")
            w.log.emit("board is powered and idle: safe to measure with a DMM")
        self._start(job)

    def _live_monitor(self):
        if getattr(self, "monitor", None) is not None and self.monitor.isVisible():
            self.monitor.raise_(); self.monitor.activateWindow()
            return
        self.monitor = LiveMonitor(self, self._payload_dir(), self.cv)
        self.monitor.show()


class BitLaneChart(QWidget):
    """DSLogic-style multi-lane step chart over a scrolling window. Each lane
    is one bit of the real bytes returned by EP6 (I/O0..I/O7). It is a value
    trace of received data, NOT sampled pin edges - the reader has no per-pin
    sampler, so the x axis is byte index, not time. Only `window` bytes are
    drawn at once so a live stream does not blur; wheel scrolls, and ID hits
    are marked as vertical ticks."""

    LANES = 8
    LEFT = 46
    cursor_moved = pyqtSignal(int)   # byte index under the mouse, -1 = none

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = b""
        self.hits = []            # byte indices where a valid ID ended
        self.cursor = -1
        self.window = 128          # bytes visible at once
        self.follow = True         # auto-scroll to newest
        self.offset = 0            # first visible byte index
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip("Real bits of received bytes; wheel scrolls, "
                        "x = byte index (not time), amber ticks = ID hits.")

    def clear(self):
        self.data = b""
        self.hits = []
        self.cursor = -1
        self.offset = 0
        self.update()

    def set_data(self, data):
        data = bytes(data)
        if data != self.data:
            self.data = data
            if self.cursor >= len(data):
                self.cursor = -1
            self._clamp_offset()
            self.update()

    def add_hit(self, index):
        self.hits.append(index)
        self.hits = self.hits[-64:]
        self.update()

    def wheel(self, steps):
        self.follow = False
        self.offset -= steps * max(1, self.window // 8)
        self._clamp_offset()
        self.update()

    def show_all(self):
        self.follow = True
        self._clamp_offset()
        self.update()

    def _clamp_offset(self):
        n = len(self.data)
        if self.follow:
            self.offset = max(0, n - self.window)
        else:
            self.offset = max(0, min(self.offset, max(0, n - self.window)))

    def _visible(self):
        n = len(self.data)
        lo = self.offset
        hi = min(n, lo + self.window)
        return lo, hi

    def _x_of(self, i):
        lo, hi = self._visible()
        x0 = self.LEFT + 2
        span = (self.width() - 4) - x0
        cnt = max(1, hi - lo - 1)
        return x0 + span * (i - lo) / cnt

    def _index_at(self, x):
        lo, hi = self._visible()
        if hi <= lo:
            return -1
        x0 = self.LEFT + 2
        span = (self.width() - 4) - x0
        if span <= 0 or x < x0:
            return -1
        cnt = max(1, hi - lo - 1)
        i = lo + round((x - x0) * cnt / span)
        return max(lo, min(hi - 1, i))

    def mouseMoveEvent(self, ev):
        i = self._index_at(int(ev.position().x()))
        if i != self.cursor:
            self.cursor = i
            self.update()
            self.cursor_moved.emit(i)

    def leaveEvent(self, ev):
        if self.cursor != -1:
            self.cursor = -1
            self.update()
            self.cursor_moved.emit(-1)

    def wheelEvent(self, ev):
        self.wheel(1 if ev.angleDelta().y() > 0 else -1)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#101418"))
        left = self.LEFT
        top = 6
        lane_h = max(6, (h - top - 22) / self.LANES)
        lo, hi = self._visible()
        n = len(self.data)
        x0 = left + 2
        span = (w - 4) - x0
        cnt = max(1, hi - lo - 1)
        for idx in self.hits:
            if lo <= idx < hi:
                hx = x0 + span * (idx - lo) / cnt
                p.setPen(QPen(QColor("#ffb300"), 1.2))
                p.drawLine(int(hx), top, int(hx), int(h - top - 16))
        if 0 <= self.cursor < n:
            cx = self._x_of(self.cursor)
            p.fillRect(int(cx) - 3, top, 6, int(h - top - 16),
                       QColor(255, 213, 79, 50))
        p.setFont(QFont("monospace", 7))
        for lane in range(self.LANES):
            y_hi = top + lane * lane_h + 2
            y_lo = top + (lane + 1) * lane_h - 2
            p.setPen(QPen(QColor("#263238"), 1))
            p.drawLine(left, int(y_lo), w - 4, int(y_lo))
            p.setPen(QPen(QColor("#607d8b"), 1))
            p.drawText(2, int((y_hi + y_lo) / 2) + 3, f"I/O{lane}")
            if hi - lo < 2:
                continue
            p.setPen(QPen(QColor("#4fc3f7"), 1.4))
            pts = QPolygonF()
            for i in range(lo, hi):
                bit = (self.data[i] >> lane) & 1
                y = y_hi if bit else y_lo
                x = x0 + span * (i - lo) / cnt
                pts.append(QPointF(x, y))
                if i + 1 < hi:
                    nb = (self.data[i + 1] >> lane) & 1
                    if nb != bit:
                        pts.append(QPointF(x, y_hi if nb else y_lo))
            p.drawPolyline(pts)
        p.setPen(QPen(QColor("#90a4ae"), 1))
        if n:
            label = (f"bytes {lo}..{hi - 1} of {n}  |  window {hi - lo}  |  "
                     f"{'follow' if self.follow else 'scroll'}  |  bits of "
                     f"received data, x = byte index (not time)")
        else:
            label = "no bytes yet - run READ ID to fill the lanes"
        p.setFont(QFont("sans-serif", 7))
        p.drawText(left, h - 6, label)


class LiveMonitor(QWidget):
    """DSLogic-style channel table populated with the real NAND interface
    signals. Names and roles are true; the state column shows whether the
    current phase of the read actually exercises each line. It is NOT a
    waveform: the two IN pipes (EP6/EP8) carry only NAND data bytes, never
    sampled pin states, so edge timing cannot be shown."""

    CHANNELS = [
        # key, signal, role, phases in which the line is exercised
        ("cle", "CLE",  "command latch enable",        ("cmd",)),
        ("ale", "ALE",  "address latch enable",        ("addr",)),
        ("ce",  "CE#",  "chip enable",                 ("idle", "cmd", "addr", "data")),
        ("re",  "RE#",  "read enable (data strobe)",   ("data",)),
        ("we",  "WE#",  "write enable (op clock)",     ("cmd", "addr")),
        ("rb",  "R/B#", "ready/busy",                  ("wait",)),
        ("wp",  "WP#",  "write protect (held)",        ("idle", "cmd", "addr", "data")),
        ("io",  "I/O[7:0]", "data bus",                ("data",)),
        ("rail", "VCC", "rail supply",                 ("idle", "cmd", "addr", "data")),
        ("vss", "VSS",  "ground",                      ("idle", "cmd", "addr", "data")),
    ]

    PHASE_LABEL = {"idle": "idle", "cmd": "command", "addr": "address",
                   "wait": "ready/busy", "data": "data out"}

    def __init__(self, parent, payload_dir, cv):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("NAND Signal Monitor (channel state, not waveform)")
        self.resize(600, 420)
        self.payload_dir = payload_dir
        self.cv = cv
        self.stop_evt = threading.Event()
        self.thread = None
        self.stats = {}
        self._lock = threading.Lock()
        self._reset()

        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["READ ID (chip design)", "idle listener (power design)"])
        bar.addWidget(self.mode)
        self.autostop = QCheckBox("stop when ID found")
        self.autostop.setToolTip("Off (default): keep running and mark every "
                                 "ID hit with an amber tick. On: freeze and "
                                 "stop at the first valid ID.")
        bar.addWidget(self.autostop)
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._toggle)
        bar.addWidget(self.run_btn)
        self.full_btn = QPushButton("Full screen")
        self.full_btn.setCheckable(True)
        self.full_btn.toggled.connect(self._toggle_full)
        bar.addWidget(self.full_btn)
        v.addLayout(bar)

        self.table = QTableWidget(len(self.CHANNELS), 4)
        self.table.setHorizontalHeaderLabels(["", "Signal", "Role", "State"])
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 22)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setFont(QFont("monospace", 9))
        for row, (_k, sig, role, _ph) in enumerate(self.CHANNELS):
            self.table.setItem(row, 0, QTableWidgetItem(""))
            self.table.setItem(row, 1, QTableWidgetItem(sig))
            self.table.setItem(row, 2, QTableWidgetItem(role))
            self.table.setItem(row, 3, QTableWidgetItem("-"))

        self.phase_lbl = QLabel("phase: -")
        self.phase_lbl.setFont(QFont("monospace", 9))
        v.addWidget(self.phase_lbl)

        self.chart = BitLaneChart(self)
        self.chart.setMinimumHeight(180)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.addWidget(self.table)
        self.split.addWidget(self.chart)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([220, 260])
        v.addWidget(self.split, 1)

        self.chart.cursor_moved.connect(self._on_cursor)
        self.decode_lbl = QLabel("decoder: move the mouse over a byte")
        self.decode_lbl.setFont(QFont("monospace", 9))
        self.decode_lbl.setWordWrap(True)
        self.decode_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.decode_lbl)

        note = QLabel("Signals and roles are the real NAND interface. State "
                      "shows whether the current read phase uses each line. "
                      "The lanes below are the real bits of received bytes "
                      "(x = byte index, not time): EP6/EP8 carry data only, so "
                      "these are not sampled pin edges.")
        note.setWordWrap(True)
        note.setFont(QFont("sans-serif", 8))
        v.addWidget(note)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(120)

    def _reset(self):
        with self._lock:
            self.stats = {"phase": None, "rail": None, "id": b"", "stream": b"",
                          "err": "", "id_mode": False, "cycles": 0,
                          "locked": False, "hits": [], "last_hit": b"",
                          "found": False}
        if hasattr(self, "chart"):
            self.chart.clear()

    def _set_phase(self, phase):
        with self._lock:
            self.stats["phase"] = phase

    def _toggle(self):
        if self.thread and self.thread.is_alive():
            self.stop_evt.set()
            self.run_btn.setEnabled(False)
            return
        self.stop_evt.clear()
        self._reset()
        self.run_btn.setText("Stop")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        id_mode = self.mode.currentText().startswith("READ ID")
        with self._lock:
            self.stats["id_mode"] = id_mode
        try:
            while not self.stop_evt.is_set():
                try:
                    found = self._cycle(id_mode)
                except (nl.usb.core.USBError, nl.ReaderError) as exc:
                    with self._lock:
                        self.stats["err"] = str(exc)
                    self._set_phase(None)
                    return
                with self._lock:
                    self.stats["cycles"] += 1
                    if found:
                        idx = len(self.stats["stream"]) - 1
                        self.stats["hits"] = self.stats.get("hits", []) + [idx]
                        self.stats["last_hit"] = self.stats["id"]
                        self.stats["found"] = True
                    if found and self.autostop.isChecked():
                        self.stats["locked"] = True
                        return
        except (nl.usb.core.USBError, nl.ReaderError) as exc:
            with self._lock:
                self.stats["err"] = str(exc)

    def _cycle(self, id_mode):
        """One cold boot holding a fresh Reader. The USB handle cannot survive
        a power cycle, so the reader is opened inside the cycle, not around it."""
        _safe_cycle(settle=0.5)
        n = self._id_size()
        with nl.Reader() as r:
            r.load_payloads(self.payload_dir)
            r.drain_in()
            r._chunked(r.power_image)
            r._out(nl.cv_to_dac(self.cv))
            self._set_phase("wait")
            try:
                e = bytes(r.dev.read(nl.EP_IN, 8, nl.IN_TIMEOUT_MS))
                if len(e) >= 4:
                    with self._lock:
                        self.stats["rail"] = nl.decode_cv(e)
            except nl.usb.core.USBError:
                pass
            if not id_mode:
                self._set_phase("idle")
                t_end = time.time() + 1.0
                while time.time() < t_end and not self.stop_evt.is_set():
                    try:
                        r.dev.read(nl.EP_IN, 512, 20)
                    except nl.usb.core.USBError:
                        pass
                return
            self._set_phase("cmd")
            r.drain_in()
            r._chunked(r.chip_image)
            r._out(r.program_block)
            self._set_phase("addr")
            r._out(r.id_tail(1, n)[1])
            self._set_phase("data")
            try:
                got = bytes(r.dev.read(nl.EP_IN, max(64, n), 1500))
            except nl.usb.core.USBError:
                got = b""
            with self._lock:
                self.stats["id"] = got[:n]
                self.stats["stream"] = (self.stats["stream"] + got)[-2048:]
            self._set_phase(None)
            return self._is_valid_id(got[:n])
        return False

    @staticmethod
    def _kind(reply):
        if not reply:
            return "?"
        hit = CHIP_DB.get(" ".join(f"{b:02x}" for b in reply[:4]))
        return f"{hit[0]} {hit[1]}" if hit else "vendor byte recognised"

    @staticmethod
    def _is_valid_id(reply):
        """True when the reply is a real chip: a database entry, or a vendor
        byte whose second byte is one that vendor actually uses."""
        if len(reply) < 2 or len(set(reply)) == 1:
            return False
        key = " ".join(f"{b:02x}" for b in reply[:4])
        if key in CHIP_DB:
            return True
        pairs = {}
        for k in CHIP_DB:
            b = bytes.fromhex(k.replace(" ", ""))
            pairs.setdefault(b[0], set()).add(b[1])
        return reply[0] in pairs and reply[1] in pairs[reply[0]]

    def _id_size(self):
        win = self.parent()
        return getattr(win, "id_size", 4) or 4

    def _refresh(self):
        alive = self.thread is not None and self.thread.is_alive()
        if not alive and self.run_btn.text() == "Stop":
            self.run_btn.setText("Start")
            self.run_btn.setEnabled(True)
        with self._lock:
            s = dict(self.stats)
        grey, green, red, orange, dark = (
            "#9e9e9e", "#2e7d32", "#c62828", "#ef6c00", "#37474f")

        if s["err"]:
            for i in range(self.table.rowCount()):
                self.table.item(i, 3).setText("error: " + s["err"])
            self.phase_lbl.setText("phase: error")
            return

        phase = s["phase"] if alive else None
        for idx in s.get("hits", []):
            if idx not in self.chart.hits:
                self.chart.add_hit(idx)
        if s["locked"] and s["id"]:
            self.phase_lbl.setText(
                f"LOCKED on {s['id'].hex(' ')}  ({self._kind(s['id'])})  "
                f"after {s['cycles']} cycle(s)")
            self.phase_lbl.setStyleSheet("color:#2e7d32;font-weight:bold")
        elif s.get("found") and alive:
            hits = len(s.get("hits", []))
            idx = s.get("hits", [-1])[-1]
            self.phase_lbl.setText(
                f"found {s['last_hit'].hex(' ')} ({self._kind(s['last_hit'])}) "
                f"at byte {idx} - hit #{hits}, still running")
            self.phase_lbl.setStyleSheet("color:#ef6c00;font-weight:bold")
        else:
            self.phase_lbl.setStyleSheet("")
            self.phase_lbl.setText(
                "phase: " + (self.PHASE_LABEL.get(phase, "-") if phase else
                             ("idle" if alive else "stopped")) +
                (f"   cycle {s['cycles']}" if s["cycles"] else ""))

        for i, (key, _sig, _role, phases) in enumerate(self.CHANNELS):
            dot = self.table.item(i, 0)
            st = self.table.item(i, 3)
            if not alive:
                dot.setText("●"); dot.setForeground(QColor(grey))
                st.setText("stopped")
                continue
            if key == "rail":
                if s["rail"] is not None:
                    val = s["rail"] - self.cv
                    dot.setText("●")
                    dot.setForeground(QColor(green if abs(val) <= 12 else orange))
                    st.setText(f"{s['rail']} cV (set {self.cv})")
                else:
                    dot.setText("●"); dot.setForeground(QColor(orange))
                    st.setText("no readback")
                continue
            if key == "vss":
                dot.setText("●"); dot.setForeground(QColor(green))
                st.setText("reference")
                continue
            if key == "io":
                has = bool(s["id"])
                dot.setText("●"); dot.setForeground(QColor(green if has else dark))
                st.setText(s["id"].hex(" ") if has else "latched (no capture)")
                continue
            active = phase in phases
            dot.setText("●")
            dot.setForeground(QColor(green if active else dark))
            st.setText("active" if active else "idle")

        self.chart.set_data(s["stream"])

    def _toggle_full(self, on):
        if on:
            self._saved_geo = self.geometry()
            self.table.setVisible(False)
            self.split.setSizes([0, 10000])
            self.full_btn.setText("Exit full screen")
            self.showMaximized()
        else:
            self.table.setVisible(True)
            self.split.setSizes([220, 260])
            self.full_btn.setText("Full screen")
            self.showNormal()
            if hasattr(self, "_saved_geo"):
                self.setGeometry(self._saved_geo)

    def _on_cursor(self, i):
        with self._lock:
            stream = self.stats.get("stream", b"")
        if i < 0 or i >= len(stream):
            self.decode_lbl.setText("decoder: move the mouse over a byte")
            return
        b = stream[i]
        bits = "".join(str((b >> k) & 1) for k in range(7, -1, -1))
        parts = [f"byte[{i}]  =  0x{b:02x}  ({b:3d})  bin {bits}"]
        # READ ID reply is 4 bytes per cycle; every 4th starts a new ID read.
        pos = i % 4
        cyc = i // 4
        if pos == 0:
            vendor = CHIP_DB.get(" ".join(f"{x:02x}" for x in
                                          stream[cyc * 4:cyc * 4 + 4]), (None,))[0]
            parts.append(f"ID cycle {cyc + 1}  byte0 = manufacturer ID"
                         + (f" ({vendor})" if vendor else ""))
        elif pos == 1:
            parts.append(f"ID cycle {cyc + 1}  byte1 = device code (high)")
        elif pos == 2:
            parts.append(f"ID cycle {cyc + 1}  byte2 = device code (mid)")
        else:
            parts.append(f"ID cycle {cyc + 1}  byte3 = device code / size")
        chunk = stream[cyc * 4:cyc * 4 + 4]
        if len(chunk) == 4:
            hit = CHIP_DB.get(" ".join(f"{x:02x}" for x in chunk))
            if hit:
                parts.append(f"-> {chunk.hex(' ')} : {hit[0]} {hit[1]}")
            else:
                parts.append(f"-> {chunk.hex(' ')} : not in database")
        self.decode_lbl.setText("   |   ".join(parts))

    def closeEvent(self, ev):
        self.stop_evt.set()
        self.timer.stop()
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    win = ReaderWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
