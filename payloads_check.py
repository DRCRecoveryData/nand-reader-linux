#!/usr/bin/env python3
"""Payload status check for the NAND reader.

Run it before any power/id/dump: it reports which bitstreams are present,
whether sizes and prefixes match the documented vendor payloads, and prints
the exact command to copy the missing ones in.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nand_linux as nl

SPEC = {
    "power_init.bin": (nl.PWR_IMAGE_LEN, nl.POWER_INIT_SHA, False),
    "nand_init.bin": (nl.CHIP_IMAGE_LEN, nl.NAND_INIT_SHA, False),
}


def check(payload_dir):
    rows = []
    missing = []
    for name, (size, prefix, swap) in SPEC.items():
        path = os.path.join(payload_dir, name)
        if not os.path.exists(path):
            rows.append((name, "MISSING", "-", "-", "-"))
            missing.append(name)
            continue
        data = open(path, "rb").read()
        digest = hashlib.sha256(data).hexdigest()
        size_ok = len(data) == size
        hash_ok = digest.startswith(prefix)
        note = "ok" if size_ok and hash_ok else (
            f"size {len(data)} != {size}" if not size_ok else
            f"sha {digest[:8]} != {prefix}")
        rows.append((name, note, len(data), digest[:8], "yes" if swap else ""))

    table = nl.id_table()
    digest = hashlib.sha256(table).hexdigest()
    note = "ok" if digest.startswith(nl.ID_TABLE_SHA) else f"sha {digest[:8]} != {nl.ID_TABLE_SHA}"
    rows.append(("(id table)", note + " (generated)", len(table), digest[:8], "yes"))
    return rows, missing


def main():
    payload_dir = sys.argv[1] if len(sys.argv) > 1 else "payloads"
    rows, missing = check(payload_dir)
    print(f"payload dir: {os.path.abspath(payload_dir)}\n")
    print(f"{'file':<18}{'status':<34}{'bytes':>10}  {'sha256':<9} swap")
    print("-" * 82)
    for name, note, size, sha, swap in rows:
        print(f"{name:<18}{note:<34}{size if isinstance(size,int) else size:>10}  {sha:<9} {swap}")
    print()
    if missing:
        print("missing " + ", ".join(missing))
        print("copy them in, e.g. from the FlashReader2 install dir:")
        print("  cp /path/to/FlashReader2/payloads/*.bin " + os.path.abspath(payload_dir) + "/")
        return 1
    print("all payloads present -- power/id/dump are unblocked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
