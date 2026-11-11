#!/usr/bin/env python3
"""After the working ID leg, try candidate READ framings and read a full page.

The CPLD answers the ID leg with 4 bytes then goes silent. Page data needs the
micro-op program to end in a NAND READ (0x00 -> addr -> 0x30) instead of
READ-ID (0x11 0x90). We do not know the exact encoding, so we enumerate a few
plausible ones on a cold cycle each and record the answer length/bytes.

All legs run through the same USB path the vendor harness relays, i.e. direct
libusb on 03eb:5903 (the proxy is a byte pipe here, so this is equivalent).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl

# READ ID sequence in the program table, for reference:
#   (0x21,0x00)(0x80,0x00)(0x11,0xFF)(0x80,0x00)
#   (0x21,0x00)(0x80,0x00)(0x11,0x90)(0x80,0x00)   <- 0x11 0x90 = READ ID
#   (0x10,0x00)(0x80,0x00)(0x20,0x00)(0x80,0x00)
#   (0x20,0x01)(0x80,0x00)(0x22,0x00)(0x80,0x00)

READ_ID_OPS = list(nl.ID_TABLE_OPS)


def make_program(ops):
    buf = bytearray(nl.PROGRAM_LEN)
    pos = 0
    for op, arg in ops:
        buf[pos] = op
        buf[pos + 1] = arg
        pos += 2
    buf[0::2], buf[1::2] = buf[1::2], buf[0::2]
    return bytes(buf)


def cold():
    try:
        nl.power_cycle(settle=nl.LONG_OFF_S)
    except nl.ReaderError:
        nl.bus_reset()


def leg(name, ops, follow, read_len, bank=1, cv=330):
    """image -> program(ops) -> descriptor(bank) -> follow frames -> read."""
    cold()
    try:
        with nl.Reader() as r:
            r.load_payloads("payloads")
            r.boot_rail(cv)
            r.drain_in()
            r._chunked(r.chip_image)
            r._out(make_program(ops))
            desc = bytearray(nl.DESCRIPTOR)
            desc[11] = 1 << (bank - 1)
            r._out(bytes(desc))
            for f in follow:
                r._out(f)
            got = b""
            while len(got) < read_len:
                try:
                    b = r._in(min(read_len - len(got), 4096))
                except Exception as exc:  # noqa: BLE001
                    got += b""
                    print(f"[{name}] stop at {len(got)}B: {exc!r}")
                    break
                if not b:
                    break
                got += bytes(b)
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] leg error {exc!r}")
        return b""
    uniq = len(set(got)) if got else 0
    print(f"[{name}] {len(got)} B, {uniq} distinct: {got[:48].hex(' ')}")
    return got


def main():
    page = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    unit = 2048
    # Candidate READ micro-op tables: put a READ (00 30) at the end of the
    # sequence, with the page address in the op args.
    read_ops = [
        (0x21, 0x00), (0x80, 0x00), (0x11, 0xFF), (0x80, 0x00),
        (0x11, 0x00), (0x80, 0x00), (0x00, 0x00), (0x80, 0x00),  # 0x11 0x00 reset?
        ((page >> 0) & 0xFF, 0x80), ((page >> 8) & 0xFF, 0x80),
        ((page >> 16) & 0xFF, 0x80),
        (0x30, 0x00), (0x80, 0x00), (0x22, 0x00), (0x80, 0x00),
    ]
    # range-control follow frames to try after the descriptor
    ctl = nl._range_ctl(page, page + 1, unit, 1)
    legs = [
        ("id-baseline", READ_ID_OPS, [], 64),
        ("read-ops", read_ops, [], unit),
        ("id+ctl", READ_ID_OPS, [bytes(ctl)], unit),
        ("read-ops+ctl", read_ops, [bytes(ctl)], unit),
        ("id+rdcmdframe", READ_ID_OPS,
         [bytes.fromhex("00 00 00 00 30 00 00 00" + "00" * 24)], unit),
    ]
    for name, ops, follow, rl in legs:
        leg(name, ops, follow, rl)


if __name__ == "__main__":
    main()
