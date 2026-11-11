#!/usr/bin/env python3
"""Check physical chip presence: rail ADC sample + per-bank READ ID.

Run as root (libusb needs the device). Cold-cycles the YKUSH before each leg
because the FPGA takes one design per power-up.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl


def power_up():
    try:
        nl.power_cycle(settle=nl.LONG_OFF_S)
    except nl.ReaderError:
        nl.bus_reset()


def rail():
    power_up()
    with nl.Reader() as r:
        r.load_payloads("payloads")
        cv = r.power(330)
    print(f"rail @330 cV -> ADC sample {cv} cV")
    return cv


def bank_id(bank):
    power_up()
    with nl.Reader() as r:
        r.load_payloads("payloads")
        ident = r.read_id(bank, "payloads")
    return bytes(ident)


def main():
    cvs = [int(a) for a in sys.argv[1:]] or [330]
    for cv in cvs:
        power_up()
        with nl.Reader() as r:
            r.load_payloads("payloads")
            got = r.power(cv)
        print(f"[rail] set {cv:3d} cV -> sample {got:4d} cV", flush=True)
    for bank in (1, 2, 3, 4):
        try:
            ident = bank_id(bank)
            print(f"[id] bank {bank}: {len(ident)} B {ident[:8].hex(' ')}", flush=True)
        except Exception as exc:  # noqa: BLE001 - report whatever the leg does
            print(f"[id] bank {bank}: ERR {exc!r}", flush=True)


if __name__ == "__main__":
    main()
