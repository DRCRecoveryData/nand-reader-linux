#!/usr/bin/env python3
"""Find the page-address encoding: does any read-program variant make the
output change with the page number?

Op vocabulary (from NAND_Reader.exe):
  0x11 cmd  NAND command byte (0x30 read, 0x90 id, 0x43 read-col, 0x46 cache)
  0x10 b    row-address cycle, byte value b
  0x13 b    column-address cycle, byte value b
  0x12 b    literal address byte (what FUN_0047b380 emits)
  0x80 00   wait
  0x21/0x20/0x22/0xF0/0x30  FPGA/DMA control

We build a plain read and place the page number three ways, then compare the
first 16 bytes across pages 0 / 100 / 1000.  A variant whose output tracks the
page is (a) live and (b) discloses the address encoding.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl

W = (0x80, 0x00)


def pairswap(buf):
    b = bytearray(buf)
    b[0::2], b[1::2] = b[1::2], b[0::2]
    return bytes(b)


def program(page, mode, row_cycles=3, col_cycles=2):
    ops = []

    def e(op, arg=0x00):
        ops.append((op & 0xFF, arg & 0xFF))

    e(0x21, 0x00); e(*W)
    e(0x11, 0xFF); e(*W)
    e(0x21, 0x00); e(*W)
    e(0x11, 0x00); e(*W)
    if mode == "r10":            # page bytes in row-address cycle args
        rb = [(page >> 16) & 0xFF, (page >> 8) & 0xFF, page & 0xFF]
        for i in range(row_cycles):
            e(0x10, rb[i % len(rb)])
            e(*W)
    elif mode == "r12":          # page bytes as 0x12 literals
        rb = [(page >> 16) & 0xFF, (page >> 8) & 0xFF, page & 0xFF]
        for b in rb:
            e(0x12, b); e(*W)
        for _ in range(row_cycles):
            e(0x10, 0x00); e(*W)
    else:                        # zero (baseline)
        for _ in range(row_cycles):
            e(0x10, 0x00); e(*W)
    for i in range(col_cycles):
        e(0x13, i); e(*W)
    e(0x11, 0x30); e(*W)         # READ
    e(0x21, 0x00); e(*W)
    e(0x20, 0x00); e(*W)
    e(0x21, 0x00); e(*W)
    for i in range(1, 6):
        e(0x30, i)
    e(0xF0, 0x00)
    e(0x30, (page >> 8) & 0xFF)
    e(0x30, page & 0xFF)
    e(0x20, 0x01); e(*W)
    e(0x22, 0x00); e(*W)

    buf = bytearray(nl.PROGRAM_LEN)
    for i, (op, arg) in enumerate(ops):
        buf[2 * i] = op
        buf[2 * i + 1] = arg
    return pairswap(bytes(buf))


def cold():
    try:
        nl.power_cycle(settle=nl.LONG_OFF_S)
    except nl.ReaderError:
        nl.bus_reset()


def read_page(page, mode, unit=2048, bank=1):
    cold()
    with nl.Reader() as r:
        r.load_payloads("payloads")
        r.boot_rail(330)
        r.drain_in()
        r._chunked(r.chip_image)
        r._out(program(page, mode))
        r._out(nl._range_ctl(page, page + 1, unit, bank))
        got = b""
        while len(got) < unit:
            try:
                b = r._in(min(unit - len(got), 4096))
            except Exception:  # noqa: BLE001
                break
            if not b:
                break
            got += bytes(b)
    return got


def main():
    pages = [int(a) for a in sys.argv[1:]] or [0, 100, 1000]
    for mode in ("zero", "r10", "r12"):
        outs = {}
        for p in pages:
            outs[p] = read_page(p, mode)
        sig = {p: d[:16].hex(" ") for p, d in outs.items()}
        same = len(set(outs[p][:256] for p in pages)) == 1
        print(f"mode {mode:5s} same_across_pages={same}")
        for p in pages:
            d = outs[p]
            print(f"   p{p:<5d} {len(d):5d}B uniq={len(set(d)):3d} {sig[p]}")


if __name__ == "__main__":
    main()
