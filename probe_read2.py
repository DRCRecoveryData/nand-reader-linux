#!/usr/bin/env python3
"""Reproduce the vendor READ program (FUN_0045e3d0) and try to read a page.

From the vendor binary (NAND_Reader.exe):
  FUN_00424f20 is the wire routine used for BOTH ID and READ:
      stream chip image (0x530dcc)
      pair-swap 0x400 pairs over the 0x800-byte program
      write program (0x800)
      write descriptor: config[0xc6..0xe5], 0x20 bytes
      read data

  Program block p = FUN_0045ed10(config) which, when the chip command string
  (config+0xa4) is empty, dispatches to FUN_0045e3d0 (generic):
      0x21 00,0x80 00, 0x11 FF,0x80 00,
      0x21 00,0x80 00,
      <setup via FUN_0045dbe0>
      0x11 00,0x80 00,
      N * (0x10 00, 0x80 00)         N = config[0]   (address cycles)
      M * (0x13 i,  0x80 00)         M = config[1]   (column cycles)
      0x11 30,0x80 00,               READ (vs 0x11 0x90 = READ ID)
      0x21 00,0x80 00, 0x20 00,0x80 00, 0x21 00,0x80 00
  then FUN_0045ed10 tail:
      0x30 01, 0x30 02, 0x30 03, 0x30 04, 0x30 05   (DMA descriptor setup)
      <address vector>
      0xF0 00,
      0x30 <hi> <lo>                                 (per-page address)
      0x20 01,0x80 00, 0x22 00,0x80 00

The probe tries this with the descriptor = the native _range_ctl (since the
runtime config[0xc6] defaults are unknown) and a couple of address-cycle counts.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl

PAIR_WAIT = (0x80, 0x00)


def pairswap(buf):
    b = bytearray(buf)
    b[0::2], b[1::2] = b[1::2], b[0::2]
    return bytes(b)


def build_generic_read(page, addr_cycles, col_cycles):
    ops = []
    def e(op, arg):
        ops.append((op & 0xFF, arg & 0xFF))

    e(0x21, 0x00); e(*PAIR_WAIT)
    e(0x11, 0xFF); e(*PAIR_WAIT)
    e(0x21, 0x00); e(*PAIR_WAIT)
    # (FUN_0045dbe0 chip-specific setup omitted: it is a virtual dispatch that
    #  emits extra ops; the generic path still works for a plain read)
    e(0x11, 0x00); e(*PAIR_WAIT)
    for _ in range(addr_cycles):          # N row-addr cycles
        e(0x10, 0x00); e(*PAIR_WAIT)
    for i in range(col_cycles):           # M column-addr cycles
        e(0x13, i); e(*PAIR_WAIT)
    e(0x11, 0x30); e(*PAIR_WAIT)          # READ
    e(0x21, 0x00); e(*PAIR_WAIT)
    e(0x20, 0x00); e(*PAIR_WAIT)
    e(0x21, 0x00); e(*PAIR_WAIT)
    # tail: DMA descriptor prefix + page address
    for i in range(1, 6):
        e(0x30, i)
    e(0xF0, 0x00)
    e(0x30, (page >> 8) & 0xFF)
    e(0x30, page & 0xFF)
    e(0x20, 0x01); e(*PAIR_WAIT)
    e(0x22, 0x00); e(*PAIR_WAIT)

    buf = bytearray(nl.PROGRAM_LEN)
    for i, (op, arg) in enumerate(ops):
        buf[2 * i] = op
        buf[2 * i + 1] = arg
    return pairswap(bytes(buf))


def descriptor(page, unit, bank):
    return nl._range_ctl(page, page + 1, unit, bank)


def cold():
    try:
        nl.power_cycle(settle=nl.LONG_OFF_S)
    except nl.ReaderError:
        nl.bus_reset()


def leg(name, page, addr_cycles, col_cycles, unit, bank, read_len):
    cold()
    try:
        with nl.Reader() as r:
            r.load_payloads("payloads")
            r.boot_rail(330)
            r.drain_in()
            r._chunked(r.chip_image)
            r._out(build_generic_read(page, addr_cycles, col_cycles))
            r._out(descriptor(page, unit, bank))
            got = b""
            while len(got) < read_len:
                try:
                    b = r._in(min(read_len - len(got), 4096))
                except Exception as exc:  # noqa: BLE001
                    print(f"[{name}] stop {len(got)}B {exc!r}")
                    break
                if not b:
                    break
                got += bytes(b)
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] err {exc!r}")
        return b""
    uniq = len(set(got)) if got else 0
    print(f"[{name}] {len(got)} B {uniq} distinct: {got[:32].hex(' ')}")
    return got


def main():
    page = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    unit = 2048
    # (addr_cycles, col_cycles): typical NAND 2K page = 5 addr cycles total,
    # e.g. 3 row + 2 col; also try 5+0 and 2+2.
    combos = [(3, 2), (5, 0)]
    got = []
    for ac, cc in combos:
        data = leg(f"ac{ac}_cc{cc}_p{page}", page, ac, cc, unit, 1, unit)
        got.append(data)
    if len(got) >= 2 and got[0] and got[0] == got[1]:
        print("NOTE: identical output across address-cycle variants")
    if got and got[0] and got[0][:4] != got[-1][:4]:
        print("DIFF: outputs differ between variants")


if __name__ == "__main__":
    main()
