#!/usr/bin/env python3
"""Issue the ID leg then read a large chunk: does data past the 4-byte ID vary
with page address (real NAND) or loop the ID / stay constant (map pattern)?
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl


def run(bank, read_len, cv=330):
    try:
        nl.power_cycle(settle=nl.LONG_OFF_S)
    except nl.ReaderError:
        nl.bus_reset()
    with nl.Reader() as r:
        r.load_payloads("payloads")
        r.boot_rail(cv)
        r.drain_in()
        r._chunked(r.chip_image)
        r._out(r.program_block)
        desc = bytearray(nl.DESCRIPTOR)
        desc[11] = 1 << (bank - 1)
        r._out(bytes(desc))
        parts = []
        want = read_len
        while want > 0:
            try:
                b = r._in(min(want, 4096))
            except Exception as exc:  # noqa: BLE001
                print(f"  read err after {read_len-want} B: {exc!r}")
                break
            if not b:
                break
            parts.append(bytes(b))
            want -= len(b)
        data = b"".join(parts)
    return data


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2048
    for bank in (1, 2):
        data = run(bank, n)
        uniq = len(set(data))
        head = data[:32].hex(" ")
        tail = data[-32:].hex(" ") if len(data) > 32 else ""
        print(f"bank {bank}: {len(data)} B, {uniq} distinct byte values")
        print(f"  head {head}")
        if tail:
            print(f"  tail {tail}")
        # does it loop the ID?
        if data[:4] == bytes.fromhex("89c40832"):
            period = data.find(bytes.fromhex("89c40832"), 1)
            print(f"  ID at offset 0, next ID at {period}")


if __name__ == "__main__":
    main()
