#!/usr/bin/env python3
"""Linux driver for the Soft-Center NAND Flash Reader 2.0 (03eb:5903).

Wire protocol per DRCRecoveryData/nand-recovery-reference:
  index.html  appendix A  (transaction maps, DAC word, descriptor, range control)
  hardware.html section 2 (EP2 OUT = host->FPGA, EP6 IN = FPGA->host)

Every transaction is whole-buffer writes followed by a read on the one pipe.
There is no opcode envelope: the sequence is the command.
"""

import argparse
import hashlib
import os
import struct
import sys
import time

import usb.core
import usb.util

VID = 0x03EB
PID = 0x5903
EP_OUT = 0x02
EP_IN = 0x86
IFACE = 0

PWR_IMAGE_LEN = 1_254_752
CHIP_IMAGE_LEN = 1_254_752
PROGRAM_LEN = 2_048

READ_TIMEOUT_MS = 20_000
WRITE_TIMEOUT_MS = 5_000
IMAGE_CHUNK = 64 << 10
LONG_OFF_S = 6.0
IMAGE_GAP_S = float(os.environ.get("NAND_IMAGE_GAP_MS", "0")) / 1000.0
# The vendor driver submits a bare bulk URB with no timeout, so it waits as
# long as the FPGA takes. The chip image answers ~1 s after the descriptor
# lands, so a short deadline turns a slow config into a fake "no reply".
IN_TIMEOUT_MS = int(os.environ.get("NAND_IN_TIMEOUT_MS", READ_TIMEOUT_MS))

POWER_INIT_SHA = "6c614063"
NAND_INIT_SHA = "d2c3da70"

# Map design injected for the ID leg. Generated, not stored: NAND_Reader.exe
# builds it from 16 (op, arg) pairs and the pair-swaps every 2,048 B program
# block on the way out. sha256 matches the documented block.
ID_TABLE_SHA = "2b1bc203"
ID_TABLE_OPS = [
    (0x21, 0x00), (0x80, 0x00), (0x11, 0xFF), (0x80, 0x00),
    (0x21, 0x00), (0x80, 0x00), (0x11, 0x90), (0x80, 0x00),
    (0x10, 0x00), (0x80, 0x00), (0x20, 0x00), (0x80, 0x00),
    (0x20, 0x01), (0x80, 0x00), (0x22, 0x00), (0x80, 0x00),
]

DESCRIPTOR = bytes.fromhex(
    "00 00 00 00 01 00 00 00 04 00 04 00 1a 00 35 77" + " ff" * 16
)


# Page READ program (NAND_Reader.exe FUN_0045e3d0 generic read): reset, three
# zero row-address cycles, two column cycles, READ 0x30, then DMA setup. The
# page number is carried in the 32-byte range/control block (bytes 0..3) and in
# the control trailer, not in the row cycles; this is the "zero" encoding in
# probe_addr.py, the one mapping that reproduces bit-exactly across cold
# cycles. Pages come back in blocks of 8: one erased page, 7 data or blank.
READ_ROW_CYCLES = 3
READ_COL_CYCLES = 2


def read_page_program(page, cols=READ_COL_CYCLES, rows=READ_ROW_CYCLES,
                      column=0):
    """2,048 B pair-swapped program block that reads one 2 KB NAND page.

    `column` is the byte offset inside the page where the read starts; 0 reads
    the main 2 KB data area, 0x840 targets the 64 B spare (OOB/ECC) area on an
    x8 2112-byte page. The column goes into the 0x13 cycles, LSB first.
    """
    ops = []

    def e(op, arg=0x00):
        ops.append((op & 0xFF, arg & 0xFF))

    def wait():
        e(0x80, 0x00)

    e(0x21, 0x00); wait()
    e(0x11, 0xFF); wait()          # reset
    e(0x21, 0x00); wait()
    e(0x11, 0x00); wait()          # read setup
    for _ in range(rows):          # row-address cycles (page is in the control)
        e(0x10, 0x00); wait()
    for c in range(cols):          # column address, LSB first
        e(0x13, (column >> (8 * c)) & 0xFF); wait()
    e(0x11, 0x30); wait()          # READ
    e(0x21, 0x00); wait()
    e(0x20, 0x00); wait()
    e(0x21, 0x00); wait()
    for i in range(1, 6):          # DMA descriptor prefix
        e(0x30, i)
    e(0xF0, 0x00)
    e(0x30, (page >> 8) & 0xFF)    # page number into the control trailer
    e(0x30, page & 0xFF)
    e(0x20, 0x01); wait()
    e(0x22, 0x00); wait()          # trigger

    buf = bytearray(PROGRAM_LEN)
    for i, (op, arg) in enumerate(ops):
        if 2 * i + 1 >= PROGRAM_LEN:
            raise ReaderError("read program overflow")
        buf[2 * i] = op
        buf[2 * i + 1] = arg
    buf[0::2], buf[1::2] = buf[1::2], buf[0::2]
    return bytes(buf)


class ReaderError(RuntimeError):
    pass


# YKUSH XS switched USB hub (Yepkit, 04d8:f0cd). 64-byte HID reports over the
# interrupt endpoints; report[0] is the command, the reply starts with 0x01.
YK_VID = 0x04D8
YK_PID = 0xF0CD
YK_CMD_STATUS = 0x21
YK_CMD_PORT_DOWN = 0x01
YK_CMD_PORT_UP = 0x11


class Ykush:
    def __init__(self):
        self.dev = usb.core.find(idVendor=YK_VID, idProduct=YK_PID)
        if self.dev is None:
            raise ReaderError("YKUSH XS not found (04d8:f0cd)")
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured

        self.ep_out = self.ep_in = None
        self.ifaces = []
        for cfg in self.dev:
            for intf in cfg:
                self.ifaces.append(intf.bInterfaceNumber)
                for ep in intf:
                    if ep.bEndpointAddress & 0x80:
                        self.ep_in = ep.bEndpointAddress
                    else:
                        self.ep_out = ep.bEndpointAddress
        if self.ep_in is None or self.ep_out is None:
            raise ReaderError("YKUSH XS: interrupt endpoints not found")
        # hid-generic claims the HID interface; the vendor tool talks to the
        # interrupt endpoints directly, so take the interface over.
        for num in self.ifaces:
            try:
                if self.dev.is_kernel_driver_active(num):
                    self.dev.detach_kernel_driver(num)
            except usb.core.USBError:
                pass
            usb.util.claim_interface(self.dev, num)

    def _cmd(self, op):
        report = bytes([op]) + bytes(63)
        self.dev.write(self.ep_out, report, 1_000)
        reply = bytes(self.dev.read(self.ep_in, 64, 2_000))
        if not reply or reply[0] != 0x01:
            raise ReaderError(f"YKUSH XS command {op:#04x} failed: {reply.hex()}")
        return reply

    def close(self):
        for num in self.ifaces:
            try:
                usb.util.release_interface(self.dev, num)
            except usb.core.USBError:
                pass
        usb.util.dispose_resources(self.dev)

    def state(self):
        return self._cmd(YK_CMD_STATUS)[1]

    def port_down(self):
        self._cmd(YK_CMD_PORT_DOWN)

    def port_up(self):
        self._cmd(YK_CMD_PORT_UP)


def power_cycle(settle=1.0, wait=15.0):
    """Toggle the YKUSH port so the FPGA gets a fresh configuration.

    The EP1C3 accepts exactly one config image per power-up, so every test leg
    starts from a cold reader. Returns once 03eb:5903 re-enumerates.

    `settle` is how long power stays off. A plain toggle (1 s) is enough for
    most boots, but after a failed boot the part sometimes keeps a residual
    state that refuses the next design until it is left unpowered for several
    seconds; callers retry with `settle=LONG_OFF_S` when a boot comes back
    silent.
    """
    yk = Ykush()
    try:
        yk.port_down()
        print("[*] YKUSH: reader power off", flush=True)
        time.sleep(settle)
        yk.port_up()
        print("[*] YKUSH: reader power on", flush=True)
    finally:
        yk.close()
    deadline = time.time() + wait
    while time.time() < deadline:
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is not None:
            time.sleep(1.5)  # let the SX2 settle, then drop our handles
            usb.util.dispose_resources(dev)
            return
        time.sleep(0.2)
    raise ReaderError(f"reader did not re-enumerate within {wait:.0f} s")


def power_cycle_fast():
    """Port toggle for callers that only need the FPGA to re-read its config.

    Keeps the settle after power-on (the SX2 needs it) but skips the wait for
    re-enumeration when the device is already back on the bus.
    """
    yk = Ykush()
    try:
        yk.port_down()
        time.sleep(0.5)
        yk.port_up()
    finally:
        yk.close()
    deadline = time.time() + 10.0
    dev = None
    while time.time() < deadline:
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is not None:
            break
        time.sleep(0.2)
    if dev is None:
        raise ReaderError("reader did not come back after a port toggle")
    time.sleep(1.5)
    usb.util.dispose_resources(dev)


def recover_wedged():
    """Rebuild the FPGA/SX2 state after a timed-out read.

    A timed-out bulk read leaves the SX2 with a pending URB and the FPGA in
    whatever state the last frame put it in; a port reset then fails because the
    reinspection stream looks like another program block. The combination that
    reliably clears it is a port reset followed by a real power cycle, so the
    next command starts from a configured-from-scratch part.
    """
    try:
        bus_reset()
    except ReaderError as exc:
        print(f"[!] port reset failed ({exc})", flush=True)
    try:
        power_cycle(settle=LONG_OFF_S)
    except ReaderError as exc:
        print(f"[!] power cycle failed ({exc})", flush=True)


def ensure_awake(attempts=3, settle=0.7):
    """Return only once the reader takes a configuration and answers.

    A timed-out leg can leave the FPGA/SX2 holding a pending URB; neither a
    bus reset nor a new handle clears it, but a YKUSH power toggle does. Try
    the reader as-is, then fall back to cold cycles until an ID answers.
    """
    last = None
    for n in range(1, attempts + 1):
        if n > 1:
            power_cycle(settle=settle)
        try:
            with Reader() as r:
                r.load_payloads("payloads")
                ident = r.read_id(1)
            if ident[:4].hex() == "89c40832":
                if n > 1:
                    print(f"[*] reader recovered after {n - 1} power cycle(s)",
                          flush=True)
                return True
            last = f"reply {ident[:4].hex()} is not the ID"
            print(f"[{n}/{attempts}] {last}; cycling", flush=True)
        except (usb.core.USBError, ReaderError) as exc:
            last = str(exc)
            print(f"[{n}/{attempts}] no reply ({exc}); cycling", flush=True)
    raise ReaderError(f"reader did not answer in {attempts} attempts: {last}")


def cv_to_dac(cv):
    # ((cv - 0x50) / 5 | 0x80) ^ 0xFF, 0x80  -> little endian 2 bytes
    word = (((cv - 0x50) // 5) | 0x80) ^ 0xFF
    return bytes([word, 0x80])


def decode_cv(reply):
    # Reply echoes the DAC word in bytes 0..1; bytes 2..3 are the ADC081 sample,
    # big endian. The sample is only meaningful with a die in the socket: an
    # empty rail sits at full scale (0x0ff0 and up) regardless of the setpoint.
    if len(reply) < 4:
        raise ReaderError(f"short DAC reply: {reply.hex()}")
    return (reply[2] << 8 | reply[3]) * 330 >> 12


def id_table():
    """2,048 B program block generated the way NAND_Reader.exe builds it."""
    buf = bytearray(PROGRAM_LEN)
    pos = 0
    for op, arg in ID_TABLE_OPS:
        if pos + 2 > PROGRAM_LEN:
            raise ReaderError("assembler table overflow")
        buf[pos] = op
        buf[pos + 1] = arg
        pos += 2
    buf[0::2], buf[1::2] = buf[1::2], buf[0::2]
    return bytes(buf)


def load_payload(path, expect_len, expect_sha, swap=False):
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) != expect_len:
        raise ReaderError(f"{path}: size {len(data)} != {expect_len}")
    digest = hashlib.sha256(data).hexdigest()
    if not digest.startswith(expect_sha):
        print(
            f"[!] {os.path.basename(path)} sha256 {digest[:8]} != expected "
            f"{expect_sha} -- sending anyway (hash-gate overridden)",
            file=sys.stderr,
        )
    return pair_swap(data) if swap else data


class Reader:
    def __init__(self):
        # A kernel driver on the vendor interface makes find() fail instead of
        # just returning the device, so clear the binding first.
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        for d in usb.core.find(find_all=True, idVendor=VID, idProduct=PID) if self.dev is None else []:
            try:
                for cfg in d:
                    for intf in cfg:
                        if d.is_kernel_driver_active(intf.bInterfaceNumber):
                            d.detach_kernel_driver(intf.bInterfaceNumber)
                self.dev = d
            except usb.core.USBError:
                pass
        if self.dev is None:
            raise ReaderError("reader not found (03eb:5903)")
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured
        usb.util.claim_interface(self.dev, IFACE)
        self.wedged = False
        self.log = os.environ.get("NAND_LOG", "") == "1"
        self.power_image = None
        self.chip_image = None
        self.program_block = None

    def close(self):
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, IFACE)
            except usb.core.USBError:
                pass  # the device went away (re-enumeration or a failed write)
            usb.util.dispose_resources(self.dev)
            self.dev = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _out(self, buf):
        if self.log:
            print(f"    OUT {len(buf):5d} {bytes(buf[:48]).hex(' ')}", file=sys.stderr)
        self.dev.write(EP_OUT, buf, WRITE_TIMEOUT_MS)

    def _in(self, n):
        return bytes(self.dev.read(EP_IN, n, IN_TIMEOUT_MS))

    def _chunked(self, buf, chunk=IMAGE_CHUNK):
        """Stream a configuration image in chunks, resending any short write."""
        off = 0
        while off < len(buf):
            part = buf[off:off + chunk]
            if self.log:
                print(f"    OUT {len(part):5d} @{off}", file=sys.stderr)
            sent = self.dev.write(EP_OUT, part, WRITE_TIMEOUT_MS)
            off += sent
            time.sleep(IMAGE_GAP_S)

    def drain_in(self, timeout_ms=150):
        self.dev.timeout = timeout_ms
        got = []
        try:
            while True:
                got.append(bytes(self.dev.read(EP_IN, 512, timeout_ms)))
        except usb.core.USBError:
            pass
        finally:
            self.dev.timeout = READ_TIMEOUT_MS
        return got

    def drain_endpoint(self, ep, timeout_ms=150, size=512, rounds=32):
        got = []
        for _ in range(rounds):
            try:
                got.append(bytes(self.dev.read(ep, size, timeout_ms)))
            except usb.core.USBError:
                break
        return got

    def drain_both(self, timeout_ms=1500):
        """Listen on EP6 then EP8; EP6 first since that is the documented pipe."""
        got6 = [b for b in self.drain_endpoint(EP_IN, timeout_ms)]
        got8 = [b for b in self.drain_endpoint(0x88, min(timeout_ms, 300))]
        return ([(f"0x{EP_IN:02x}", b) for b in got6]
                + [("0x88", b) for b in got8])

    def load_payloads(self, payload_dir):
        self.power_image = load_payload(
            os.path.join(payload_dir, "power_init.bin"), PWR_IMAGE_LEN, POWER_INIT_SHA)
        self.chip_image = load_payload(
            os.path.join(payload_dir, "nand_init.bin"), CHIP_IMAGE_LEN, NAND_INIT_SHA)
        self.program_block = id_table()
        digest = hashlib.sha256(self.program_block).hexdigest()
        if not digest.startswith(ID_TABLE_SHA):
            print(f"[!] id table sha256 {digest[:8]} != {ID_TABLE_SHA}", file=sys.stderr)

    def _load_design(self, image, tail, read_len=4):
        """Stream a configuration image, then each trailing blob, then read.

        Only the first attempt is streamed: the FPGA configures once per
        power-up, so re-sending after a completed configuration would just
        refill a dead FIFO and wedge the SX2.
        """
        self._chunked(image)
        for blob in tail:
            self._out(blob)
        return self._in(read_len)

    def power(self, cv):
        """Stream the power image, then a DAC word, then read the echo sample."""
        return decode_cv(self._load_design(self.power_image, [cv_to_dac(cv)]))

    def power_down(self, payload_dir="payloads"):
        """Stream the power image on this handle, write the inverted word and
        read the 2-byte echo (vendor 0x424ca0)."""
        self._chunked(self.power_image)
        self._out(b"\xff\xff")
        return self._in(2)

    def open_power_down(self, payload_dir="payloads"):
        """Vendor 0x424ca0: fresh handle, power image, inverted word, 2-byte
        echo. The echo carries the DAC word's inverse in bytes 0..1.
        """
        self.close()
        Reader.__init__(self)
        self.load_payloads(payload_dir)
        self._chunked(self.power_image)
        self._out(b"\xff\xff")
        return self._in(2)

    def id_tail(self, bank=1):
        desc = bytearray(DESCRIPTOR)
        desc[11] = 1 << (bank - 1)
        return [self.program_block, bytes(desc)]

    def read_id(self, bank=1, payload_dir="payloads", listen_ms=None):
        """ID leg, as NAND_Reader.exe 0x424f20 orders it: the chip image, then
        the 2,048 B table, then the descriptor, then a read.

        The handle is deliberately not reopened here. The only run that
        returned 89 C4 08 32 was on the caller's existing handle, after
        draining EP6, then chip image + table + descriptor + a 64-byte read;
        every close/reopen variant returned 00 00 00 00. Reopening resets the
        SX2 FIFO state that the configuration needs.
        """
        if self.chip_image is None:
            self.load_payloads(payload_dir)
        self.boot_rail()
        self.drain_in()
        self._chunked(self.chip_image)
        self._out(self.program_block)
        self._out(self.id_tail(bank)[1])
        return bytes(self.dev.read(EP_IN, 64,
                                   listen_ms or int(os.environ.get("NAND_ID_TIMEOUT_MS",
                                                                   30_000))))

    def boot_rail(self, cv=330):
        """Power design first, on its own configuration slot.

        A cold SX2/FPGA only answers the chip design after the power design has
        been streamed once on a fresh handle; without it the chip leg comes back
        all zeros or silent. Failures are tolerated, the chip leg re-checks.
        """
        try:
            self.drain_in()
            self._chunked(self.power_image)
            self._out(cv_to_dac(cv))
            self.dev.read(EP_IN, 8, READ_TIMEOUT_MS)
            self.drain_in()
        except usb.core.USBError:
            self.drain_in()

    def boot_power(self, cv=330):
        """Stream the power design and read its echo (two bytes minimum).

        A reader that has just been toggled does not always take the next
        design, but it always takes the power image and answers it. Booting
        this design first puts the FPGA in a known state before the leg that
        actually matters. Returns the echo bytes.
        """
        self.drain_in()
        self._chunked(self.power_image)
        self._out(cv_to_dac(cv))
        return bytes(self.dev.read(EP_IN, 8, IN_TIMEOUT_MS))

    def dump_unit(self, page, unit, bank=1, flags=0, re_up=0x22):
        """Configure the chip design and collect exactly one unit for `page`."""
        ctl = _range_ctl(page, page + 1, unit, bank, flags, re_up)
        self.boot_rail()
        self.drain_in()
        self._chunked(self.chip_image)
        self._out(self.program_block)
        self._out(bytes(ctl))
        got = b""
        while len(got) < unit:
            part = self._in(unit - len(got))
            if not part:
                return None
            got += part
        return got

    def read_page(self, page, unit=2048, bank=1, column=0):
        """Read one NAND page via the vendor READ program (0x30).

        One control block per boot, so the caller has to supply a freshly
        powered reader. Returns exactly `unit` bytes, or None on a short read.
        The FPGA ignores the 0x13 column cycles, so `column` has no effect: the
        read always starts at the page start and the spare area is reached by
        using unit=2176 (2048 data + 128 spare), not by moving the column.
        """
        ctl = _range_ctl(page, page + 1, unit, bank)
        self.drain_in()
        self._chunked(self.chip_image)
        self._out(read_page_program(page, column=column))
        self._out(bytes(ctl))
        got = b""
        while len(got) < unit:
            part = self._in(unit - len(got))
            if not part:
                return None
            got += part
        return got

    def dump(self, out_path, start_page, end_page, unit, bank=1, flags=0,
             re_up=0x22, chunk_pages=None):
        """Range-control dump. Reference frame: flags 0, re word 0x3522.

        bytes 0..3 start LE | 4..7 end LE | 8..9 unit LE | 10 flags
        11 bank bit | 14 0x35 | 15 (re_down<<4)|re_up | 16..31 identity skip
        """
        ctl = _range_ctl(start_page, end_page, unit, bank, flags, re_up)

        # The chip image gets the only configuration slot, so the rail is not
        # raised here; the power design would consume it and the chip design
        # would answer 00 00 00 00.
        self._chunked(self.chip_image)
        self._out(self.program_block)
        self._out(bytes(ctl))

        total = (end_page - start_page) * unit
        chunk = min(chunk_pages * unit if chunk_pages else total, 1 << 20)
        if chunk < unit:
            chunk = unit
        chunk = (chunk // unit) * unit
        written = 0
        t0 = time.time()
        with open(out_path, "wb") as fh:
            while written < total:
                want = min(chunk, total - written)
                buf = self._in(want)
                if not buf:
                    raise ReaderError(f"short delivery at {written} B")
                if len(buf) > want:
                    buf = buf[:want]
                fh.write(buf)
                written += len(buf)
                pct = 100.0 * written / total
                sys.stderr.write(f"\r[*] {written}/{total} B ({pct:5.1f}%) "
                                 f"{written/1e6/max(time.time()-t0,1e-9):.1f} MB/s")
                sys.stderr.flush()
        sys.stderr.write("\n")
        return written


def cmd_info(args):
    with Reader() as r:
        print(f"reader: {r.dev.manufacturer} / {r.dev.product}")
        print(f"  EP_OUT 0x{EP_OUT:02x}  EP_IN 0x{EP_IN:02x}  (EP4/EP8 unreachable)")
        print("  claimed OK, bus usable")


def cmd_probe(args):
    """Safe bring-up probe: no payloads required, no rail changes.

    A bare DAC word with no image in front of it is dropped by the device, so
    the read must time out. A reply here means the pipe is not what the docs
    describe -- worth knowing before any image goes out.
    """
    with Reader() as r:
        r.drain_in()
        print("[*] sending bare DAC word (expected: dropped, read times out)")
        r._out(cv_to_dac(330))
        try:
            reply = r._in(4)
            print(f"    reply: {reply.hex()}  <-- unexpected")
        except usb.core.USBError as exc:
            print(f"    no reply as documented ({exc.errno})")
        print("[*] sending bare 0xFFFF (expected: dropped)")
        r._out(b"\xff\xff")
        try:
            reply = r._in(4)
            print(f"    reply: {reply.hex()}  <-- unexpected")
        except usb.core.USBError as exc:
            print(f"    no reply as documented ({exc.errno})")
        print("[*] listening for a configured-FPGA beacon (custom bitstream only)")
        got = r.drain_in(timeout_ms=1_000)
        if got:
            for b in got:
                print(f"    IN: {b.hex()}")
        else:
            print("    nothing (vendor image sends no beacon, or FPGA unconfigured)")


def cmd_power(args):
    if args.cycle:
        power_cycle()
    elif not os.environ.get("NAND_NO_RESET"):
        try:
            bus_reset()
        except ReaderError:
            power_cycle()
        ensure_awake(args.retries)
    with Reader() as r:
        r.load_payloads(args.payloads)
        cv = r.power(args.cv)
        print(f"[*] rail up: {cv} cV")
    if not args.keep:
        # The rail stays at the programmed voltage until power is removed, and
        # the design is already configured so no second image can land. A port
        # toggle is the only way to drop it.
        power_cycle()
        print("[*] rail down: port toggled")


def cmd_id(args):
    """One bank per session: the vendor sequence is a single ID transaction.

    Each attempt needs its own power-up: a handle that has already streamed a
    design (or been left on a timed-out read) holds the FPGA in a state that
    answers nothing, so a miss is retried from a YKUSH cold cycle.
    """
    attempts = args.retries if args.retries is not None else (30 if args.cycle else 1)
    for n in range(1, attempts + 1):
        try:
            if n > 1:
                power_cycle(settle=LONG_OFF_S)
            elif args.cycle:
                power_cycle()
            else:
                if not os.environ.get("NAND_NO_RESET"):
                    bus_reset()
        except ReaderError as exc:
            print(f"[{n}/{attempts}] power-up failed ({exc})", flush=True)
            continue
        try:
            with Reader() as r:
                r.load_payloads(args.payloads)
                ident = r.read_id(1, args.payloads)
        except (usb.core.USBError, ReaderError) as exc:
            print(f"[{n}/{attempts}] no reply ({exc}); power-cycling", flush=True)
            continue
        first = ident[:4]
        if first.hex() == "89c40832":
            print(f"[{n}/{attempts}] bank 1: {len(ident)} B, ID {first.hex(' ')} [OK]")
            return 0
        print(f"[{n}/{attempts}] {len(ident)} B, first four {first.hex(' ')} [no ID]",
              flush=True)
        if args.cycle:
            continue
        # A port reset is not always enough after a long run of design boots;
        # fall back to a real power cycle instead of giving up.
        try:
            power_cycle(settle=LONG_OFF_S)
        except ReaderError as exc:
            print(f"[{n}/{attempts}] power cycle failed ({exc})", flush=True)
        attempts = max(attempts, 3)
    raise ReaderError(f"no ID in {attempts} attempts")


def cmd_sweep(args):
    """Try candidate command framings on the map design, one per attempt.

    The FPGA takes one configuration per power-up, so each candidate needs a
    fresh boot: stream everything, then listen for listen_ms. A reader left in
    a dead state cannot be configured again, so the run starts from a port
    toggle.
    """
    power_cycle()
    with Reader() as r:
        r.load_payloads(args.payloads)
        img = r.chip_image
        table = r.program_block
        cands = {
            "image-only": [],
            "desc": [bytes(DESCRIPTOR)],
            "table+desc": [table, bytes(DESCRIPTOR)],
            "table+ctl": [table, _range_ctl(0, 1, args.unit)],
            "dac": [cv_to_dac(args.cv)],
            "table+dac": [table, cv_to_dac(args.cv)],
            "hdr30+09": [_hdr(0x30, 0x09)],
            "table+hdr30+09": [table, _hdr(0x30, 0x09)],
        }
        if args.only not in cands:
            raise ReaderError(f"unknown candidate {args.only!r}; one of {', '.join(cands)}")
        tail = cands[args.only]
        print(f"[*] streaming chip image + {args.only} "
              f"({sum(len(b) for b in tail)} B tail), then listening on 86/88")
        r._chunked(img)
        for b in tail:
            r._out(b)
        got = r.drain_both(timeout_ms=args.listen_ms)
        for ep, b in got:
            print(f"    IN {ep} {len(b):5d} {b[:64].hex(' ')}{' ...' if len(b) > 64 else ''}")
        if not got:
            print(f"    nothing back on either pipe in {args.listen_ms} ms")


def cmd_ykush(args):
    if args.action == "cycle":
        power_cycle()
        print("[*] reader back on the bus")
        return
    yk = Ykush()
    if args.action == "off":
        yk.port_down()
    elif args.action == "on":
        yk.port_up()
    print(f"[*] YKUSH XS port state: {yk.state()}")


def cmd_frame(args):
    """Stream the image, then one hex frame, then listen."""
    if args.cycle:
        power_cycle()
    frame = bytes.fromhex(args.hex)
    with Reader() as r:
        r.load_payloads(args.payloads)
        r.drain_in()
        img = r.power_image if args.design == "power" else r.chip_image
        print(f"[*] {args.design} image + {len(frame)} B frame, then listening")
        r._chunked(img)
        r._out(frame)
        got = r.drain_in(timeout_ms=args.listen_ms)
        for b in got:
            print(f"    IN {len(b):5d} {b.hex(' ')}")
        if not got:
            print(f"    nothing back in {args.listen_ms} ms")


def cmd_bare(args):
    """Stream one image and nothing else, then listen."""
    if args.cycle:
        power_cycle()
    with Reader() as r:
        r.load_payloads(args.payloads)
        r.drain_in()
        img = r.power_image if args.design == "power" else r.chip_image
        print(f"[*] streaming {args.design} image alone, listening for any output")
        r._chunked(img)
        got = r.drain_in(timeout_ms=args.listen_ms)
        for b in got:
            print(f"    IN {len(b):5d} {b[:64].hex(' ')}")
        if not got:
            print(f"    nothing back in {args.listen_ms} ms")


def _hdr(magic, opcode):
    h = bytearray(48)
    h[0] = magic
    h[1] = opcode
    return bytes(h)


def _range_ctl(start_page, end_page, unit, bank=1, flags=0, re_up=0x22):
    ctl = bytearray(32)
    struct.pack_into("<I", ctl, 0, start_page)
    struct.pack_into("<I", ctl, 4, end_page)
    struct.pack_into("<H", ctl, 8, unit)
    ctl[10] = flags
    ctl[11] = 1 << (bank - 1)
    ctl[14] = 0x35
    ctl[15] = re_up
    ctl[16:] = b"\xff" * 16
    return bytes(ctl)

SYNTHETIC_PATTERNS = (
    bytes.fromhex("89c40832a6000000"),  # the map loops the ID it just read
    bytes.fromhex("2c01020304000000"),  # an alternating counter with no die behind it
    bytes.fromhex("2727272727272727"),  # 0x27 filler seen on a floating bus
)


def synthetic_period(buf):
    """Smallest p where the whole buffer is buf[:p] repeated, or 0 if none.

    A real page is never one group repeated for its full length, so any exact
    period over a whole unit means the FPGA made the reply up. Powers of two
    are tested because the map's own replies are 8 B frames and 0x27/0x00
    fills land on 256.
    """
    n = len(buf)
    if n < 32:
        return 0
    p = 8
    while p <= n // 4:
        head = buf[:p]
        if all(buf[i:i + p] == head for i in range(p, n - p + 1, p)):
            return p
        p *= 2
    return 0


def check_real(buf, page):
    """Raise ReaderError when a unit is a known synthetic reply."""
    for pat in SYNTHETIC_PATTERNS:
        if buf.startswith(pat):
            raise ReaderError(
                f"page {page}: map returned a synthetic {pat.hex()} pattern, "
                f"not die data (see README 'Dump')")
    period = synthetic_period(buf)
    if period:
        raise ReaderError(
            f"page {page}: reply repeats every {period} B for the whole unit, "
            f"so it is an FPGA pattern, not a page read")


def majority_vote(reads):
    """Per-bit majority over several reads of the same page.

    Marginal cells resolve to different single bits from one read to the next;
    the majority value of each bit is the best estimate of what is stored.
    Returns (voted_bytes, marginal_bits) where marginal_bits counts the bits
    that were not identical across every read.
    """
    n = len(reads)
    length = len(reads[0])
    out = bytearray(length)
    marginal = 0
    for i in range(length):
        col = [r[i] for r in reads]
        byte = 0
        for b in range(8):
            ones = sum((v >> b) & 1 for v in col)
            if len(set((v >> b) & 1 for v in col)) > 1:
                marginal += 1
            if ones * 2 >= n:
                byte |= 1 << b
        out[i] = byte
    return bytes(out), marginal


def cmd_readp(args):
    """Read pages one per cold cycle with the vendor READ program."""
    out_dir = args.out
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    votes = max(1, args.votes)
    for page in args.pages:
        reads = []
        for _ in range(votes):
            try:
                power_cycle(settle=LONG_OFF_S)
            except ReaderError:
                bus_reset()
            with Reader() as r:
                r.load_payloads("payloads")
                r.boot_rail(args.cv)
                reads.append(r.read_page(page, unit=args.unit, bank=args.bank))
        if any(x is None for x in reads):
            print(f"p{page}: SHORT", flush=True)
            continue
        if votes > 1:
            d, marginal = majority_vote(reads)
        else:
            d, marginal = reads[0], 0
        if d.count(0xFF) == len(d):
            cls = "FF"
        elif d.count(0x00) == len(d):
            cls = "00"
        else:
            cls = "PROG"
        sha = hashlib.sha256(d).hexdigest()[:16]
        extra = f" votes={votes} marginal={marginal}" if votes > 1 else ""
        print(f"p{page}: {cls} len={len(d)} ff={d.count(0xFF)} "
              f"00={d.count(0x00)} sha={sha} head={d[:8].hex(' ')}{extra}",
              flush=True)
        if out_dir:
            with open(os.path.join(out_dir, f"page_{page:07d}.bin"), "wb") as fh:
                fh.write(d)


def cmd_dump(args):
    """Dump a page range.

    The map design streams one long range from a single control frame: stream
    the chip image, the 2,048 B table, then `_range_ctl`, then read
    (end - start) * unit bytes. If the stream stalls after at least one full
    unit the reader is cold-booted and the range resumes at the next page.

    With --verify a synthetic reply stops the dump instead of being written out
    as if it were data, and --report prints a per-pattern tally.
    """
    tally = {}
    stalls = 0
    boots = 0
    got_unit = 0
    total = (args.end - args.start) * args.unit
    page = args.start
    with open(args.out, "wb") as fh:
        while page < args.end:
            if page == args.start:
                if args.cycle:
                    power_cycle()
                else:
                    try:
                        bus_reset()
                    except ReaderError:
                        power_cycle()
                    ensure_awake(args.retries)
            else:
                power_cycle_fast()
            boots += 1
            want = (args.end - page) * args.unit
            try:
                with Reader() as r:
                    r.load_payloads(args.payloads)
                    r.boot_rail(args.cv)
                    r.drain_in()
                    r._chunked(r.chip_image)
                    r._out(r.program_block)
                    r._out(bytes(_range_ctl(page, args.end, args.unit,
                                            args.bank, args.flags, args.re_up)))
                    while got_unit < want:
                        chunk = min(want - got_unit, 1 << 20)
                        buf = r._in(chunk)
                        if not buf:
                            break
                        if got_unit == 0:
                            key = buf[:8].hex()
                            tally[key] = tally.get(key, 0) + 1
                            if args.verify:
                                check_real(buf, page)
                        fh.write(buf)
                        got_unit += len(buf)
                        done = (page - args.start) * args.unit + got_unit
                        print(f"[page {page}] {done}/{total} B "
                              f"({100.0 * done / total:5.1f}%)", flush=True)
            except (usb.core.USBError, ReaderError) as exc:
                print(f"[page {page}] {exc}", flush=True)
                if args.verify and isinstance(exc, ReaderError) and "synthetic" in str(exc):
                    fh.close()
                    os.remove(args.out)
                    raise
            if got_unit < want:
                stalls += 1
            page += got_unit // args.unit
            fh.flush()
            if not got_unit:
                fh.close()
                os.remove(args.out)
                raise ReaderError(f"page {page}: no data")
            got_unit = 0
    print(f"[*] wrote {os.path.getsize(args.out)} B to {args.out}")
    with open(args.out, "rb") as fh:
        print(f"    sha256 {hashlib.sha256(fh.read()).hexdigest()}")
    if args.report:
        print(f"[*] {boots} boot(s), {stalls} stopped after a partial read")
        if tally:
            print("[*] first-unit signatures:")
            for key, count in sorted(tally.items(), key=lambda kv: -kv[1]):
                print(f"    {key}  x{count}")
        if boots and stalls == boots:
            print("[!] every boot delivered fewer bytes than asked: the map is "
                  "not streaming the range (see README 'Dump')")


def cmd_busreset(args):
    """Reset the USB port without removing power (usbreset / unbind+rebind)."""
    bus_reset()


def bus_reset():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise ReaderError("reader not found on the bus")
    try:
        dev.reset()
        print("[*] USB port reset issued", flush=True)
    except usb.core.USBError as exc:
        print(f"[!] dev.reset(): {exc}")
    time.sleep(1.5)
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is not None:
        usb.util.dispose_resources(dev)
    print(f"[*] reader {'back on the bus' if dev is not None else 'missing after reset'}")


def main():
    ap = argparse.ArgumentParser(description="Soft-Center NAND Flash Reader 2.0 on Linux")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="describe the USB device").set_defaults(fn=cmd_info)
    sub.add_parser("probe", help="safe bring-up probe, no payloads").set_defaults(fn=cmd_probe)
    sub.add_parser("busreset", help="USB port reset, no power toggle").set_defaults(fn=cmd_busreset)

    p = sub.add_parser("power", help="raise/lower the die rail")
    p.add_argument("--cv", type=int, default=330)
    p.add_argument("--payloads", default="payloads")
    p.add_argument("--keep", action="store_true", help="leave the rail up")
    p.add_argument("--cycle", action="store_true",
                   help="power-cycle the reader through the YKUSH XS first")
    p.add_argument("--retries", type=int, default=3,
                   help="cold cycles allowed to get the reader answering (default 3)")
    p.set_defaults(fn=cmd_power)

    i = sub.add_parser("id", help="read NAND ID from each bank")
    i.add_argument("--cv", type=int, default=330)
    i.add_argument("--payloads", default="payloads")
    i.add_argument("--cycle", action="store_true",
                   help="power-cycle the reader through the YKUSH XS first")
    i.add_argument("--retries", type=int, default=None,
                   help="cold cycles to try (default 30 with --cycle, else 1)")
    i.set_defaults(fn=cmd_id)

    d = sub.add_parser("dump", help="dump a page range")
    d.add_argument("--cv", type=int, default=330)
    d.add_argument("--payloads", default="payloads")
    d.add_argument("--out", required=True)
    d.add_argument("--start", type=int, default=0)
    d.add_argument("--end", type=int, required=True)
    d.add_argument("--unit", type=int, required=True, help="wire page size")
    d.add_argument("--bank", type=int, default=1)
    d.add_argument("--flags", type=lambda s: int(s, 0), default=0)
    d.add_argument("--re-up", type=lambda s: int(s, 0), default=0x22)
    d.add_argument("--cycle", action="store_true",
                   help="power-cycle the reader through the YKUSH XS first")
    d.add_argument("--retries", type=int, default=3,
                   help="power-ups to try per unit (default 3)")
    d.add_argument("--verify", action="store_true",
                   help="stop instead of writing a known synthetic pattern")
    d.add_argument("--report", action="store_true",
                   help="print a tally of first-unit signatures")
    d.set_defaults(fn=cmd_dump)

    rp = sub.add_parser("readp", help="read NAND pages via the READ program")
    rp.add_argument("pages", nargs="+", type=int)
    rp.add_argument("--out", help="directory to write page_<n>.bin files")
    rp.add_argument("--bank", type=int, default=1)
    rp.add_argument("--unit", type=int, default=2048)
    rp.add_argument("--cv", type=int, default=330)
    rp.add_argument("--votes", type=int, default=1,
                    help="reads per page for per-bit majority vote (odd, e.g. 15)")
    rp.set_defaults(fn=cmd_readp)

    f = sub.add_parser("frame", help="stream the image + one hex frame, listen")
    f.add_argument("--hex", required=True)
    f.add_argument("--design", choices=["power", "chip"], default="power")
    f.add_argument("--payloads", default="payloads")
    f.add_argument("--listen-ms", type=int, default=1500)
    f.add_argument("--cycle", action="store_true")
    f.set_defaults(fn=cmd_frame)

    b = sub.add_parser("bare", help="stream the chip image with no command, listen")
    b.add_argument("--payloads", default="payloads")
    b.add_argument("--design", choices=["power", "chip"], default="chip")
    b.add_argument("--listen-ms", type=int, default=1500)
    b.add_argument("--cycle", action="store_true")
    b.set_defaults(fn=cmd_bare)

    s = sub.add_parser("sweep", help="try one command framing on the map design")
    s.add_argument("--payloads", default="payloads")
    s.add_argument("--only", required=True)
    s.add_argument("--cv", type=int, default=330)
    s.add_argument("--unit", type=int, default=4096)
    s.add_argument("--listen-ms", type=int, default=1500)
    s.set_defaults(fn=cmd_sweep)

    y = sub.add_parser("ykush", help="YKUSH XS port control")
    y.add_argument("action", choices=["status", "on", "off", "cycle"])
    y.set_defaults(fn=cmd_ykush)

    args = ap.parse_args()
    try:
        try:
            args.fn(args)
        except usb.core.USBError as exc:
            if exc.errno != 110 or args.fn in (cmd_probe, cmd_ykush):
                raise
            print("[!] read timed out; giving the FPGA a fresh configuration slot",
                  file=sys.stderr)
            recover_wedged()
            args.fn(args)
    except ReaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except usb.core.USBError as exc:
        if exc.errno == 110:
            print("usb timeout: the FPGA never answered the read even after a "
                  "recovery power cycle; check the reader is still plugged in.",
                  file=sys.stderr)
        else:
            print(f"usb error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"missing payload: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
