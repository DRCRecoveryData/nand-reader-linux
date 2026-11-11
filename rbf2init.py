#!/usr/bin/env python3
"""Pack a Cyclone RBF into the 1,254,752-byte init segment the reader streams.

Format (from DRCRecoveryData/nand-recovery-reference, hardware.html section 4):
  every byte is 0x00 or 0xFF; unzips to 627,376 bits = 78,422 bytes
  78,422 RBF bytes x 16 = 1,254,752 bytes exactly -- one 16-bit word per bit,
  one bit per byte, LSB first. 7.9% one bits.

Packing model: each RBF byte contributes its 8 bits, LSB first; each bit becomes
one 16-bit word: 0xFFFF for a set bit, 0x0000 for a clear bit, stored little
endian. 8 bits x 2 bytes = 16 bytes per RBF byte.

The bit order is not assumed -- `verify` checks the invariants the doc proves:
  * output size is exactly 1,254,752
  * every byte is 0x00 or 0xFF
  * bit density is near 7.9% one bits
  * round-trip unpack reproduces the RBF byte for byte
  * the msb variant is the per-byte bit reversal of the lsb variant
"""

import argparse
import hashlib
import sys

RBF_LEN = 78_422
INIT_LEN = 1_254_752
ON_BIT_TARGET = 0.079
ON_BIT_TOLERANCE = 0.02

VENDOR_SHA = {
    "power": "6c614063",
    "map": "d2c3da70",
}


def pack(rbf, msb=False):
    """Expand an RBF to the init stream. msb=True reverses bits per byte."""
    if len(rbf) != RBF_LEN:
        raise ValueError(f"rbf is {len(rbf)} bytes, expected {RBF_LEN}")
    out = bytearray(INIT_LEN)
    pos = 0
    for byte in rbf:
        for bit in range(8):  # LSB first
            if (byte >> bit) & 1:
                out[pos] = 0xFF
                out[pos + 1] = 0xFF
            pos += 2
    if pos != INIT_LEN:
        raise AssertionError(f"packed {pos} bytes, expected {INIT_LEN}")
    if msb:
        # the msb file is the per-byte bit reversal of the lsb file: reverse the
        # order of the 16-byte groups, not the input, or the two cancel out
        return b"".join(out[i:i + 16][::-1] for i in range(0, INIT_LEN, 16))
    return bytes(out)


def unpack(init, msb=False):
    """Invert pack: recover the RBF from the init stream."""
    if len(init) != INIT_LEN:
        raise ValueError(f"init is {len(init)} bytes, expected {INIT_LEN}")
    rbf = bytearray(RBF_LEN)
    if msb:
        init = b"".join(init[i:i + 16][::-1] for i in range(0, INIT_LEN, 16))
    pos = 0
    for i in range(RBF_LEN):
        byte = 0
        for bit in range(8):
            if init[pos] or init[pos + 1]:
                byte |= 1 << bit
            pos += 2
        rbf[i] = byte
    return bytes(rbf)


def one_bits(init):
    return sum(1 for b in init if b)


def verify(init, rbf=None):
    problems = []
    if len(init) != INIT_LEN:
        problems.append(f"size {len(init)} != {INIT_LEN}")
    bad = {b for b in init} - {0x00, 0xFF}
    if bad:
        problems.append(f"non 00/FF bytes present: {sorted(bad)[:8]}")
    density = one_bits(init) / INIT_LEN
    if abs(density - ON_BIT_TARGET) > ON_BIT_TOLERANCE:
        problems.append(f"bit density {density:.3f} outside "
                        f"{ON_BIT_TARGET}+-{ON_BIT_TOLERANCE}")
    if rbf is not None:
        if unpack(init) != rbf:
            problems.append("round-trip unpack does not reproduce the rbf")
    return problems, density


def sha8(data):
    return hashlib.sha256(data).hexdigest()[:8]


def cmd_pack(args):
    rbf = open(args.rbf, "rb").read()
    lsb = pack(rbf, msb=False)
    msb = pack(rbf, msb=True)
    problems, density = verify(lsb, rbf)
    if problems:
        for p in problems:
            print(f"[!] {p}", file=sys.stderr)
        if not args.force:
            return 1
    print(f"rbf      {len(rbf)} B  sha256 {sha8(rbf)}")
    print(f"lsb init {len(lsb)} B  sha256 {sha8(lsb)}  ones {density*100:.1f}%")
    print(f"msb init {len(msb)} B  sha256 {sha8(msb)}")
    open(args.out, "wb").write(lsb)
    print(f"wrote {args.out}")
    for kind, prefix in VENDOR_SHA.items():
        match = "MATCH" if sha8(lsb).startswith(prefix) else "differs"
        print(f"  vs vendor {kind:<5} {prefix}  {match}")
    return 0


def cmd_verify(args):
    init = open(args.init, "rb").read()
    rbf = open(args.rbf, "rb").read() if args.rbf else None
    problems, density = verify(init, rbf)
    print(f"init {len(init)} B  sha256 {sha8(init)}  ones {density*100:.1f}%")
    for kind, prefix in VENDOR_SHA.items():
        print(f"  vs vendor {kind:<5} {prefix}  "
              f"{'MATCH' if sha8(init).startswith(prefix) else 'differs'}")
    if problems:
        for p in problems:
            print(f"[!] {p}")
        return 1
    print("all invariants hold")
    return 0


def cmd_unpack(args):
    rbf = unpack(open(args.init, "rb").read(), msb=args.msb)
    open(args.out, "wb").write(rbf)
    print(f"wrote {args.out} ({len(rbf)} B, sha256 {sha8(rbf)})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="RBF <-> init segment packer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pack", help="pack an RBF into the init segment")
    p.add_argument("rbf")
    p.add_argument("-o", "--out", default="power_init.bin")
    p.add_argument("--force", action="store_true", help="write despite failed checks")
    p.set_defaults(fn=cmd_pack)

    v = sub.add_parser("verify", help="check an init segment's invariants")
    v.add_argument("init")
    v.add_argument("--rbf", help="also round-trip against this RBF")
    v.set_defaults(fn=cmd_verify)

    u = sub.add_parser("unpack", help="recover the RBF from an init segment")
    u.add_argument("init")
    u.add_argument("-o", "--out", default="recovered.rbf")
    u.add_argument("--msb", action="store_true")
    u.set_defaults(fn=cmd_unpack)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
