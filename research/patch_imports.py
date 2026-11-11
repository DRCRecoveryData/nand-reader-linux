#!/usr/bin/env python3
"""Redirect the vendor tool's device I/O imports to nandcapsys.dll.

Patches NAND_Reader.exe's import table so CreateFileA/WriteFile/ReadFile/
CloseHandle resolve from nandcapsys.dll instead of KERNEL32. The shim records
every write to \\\\.\\NAND_Flash_Reader2 into NAND_CAP and answers reads, so the
tool runs its full power/ID sequence with no hardware and no kernel driver.

The exe is fixed-base with no relocations, and the IAT is in .rdata, which is
writable at load time, so a straight name swap in the ILT is enough: the loader
re-resolves each thunk by name from the DLL named in its descriptor.
"""

import shutil
import struct
import sys

SRC = "NAND_Reader.exe"
DST = "NAND_Reader_capture.exe"
SHIM = "nandcapsys.dll"
TARGETS = {"CreateFileA", "WriteFile", "ReadFile", "CloseHandle"}

SECS = [(0x1000, 0xAE6A1, 0x400, 0xAE800), (0xB0000, 0x2E58E, 0xAEC00, 0x2E600),
        (0xDF000, 0x77C20, 0xDD200, 0x76000), (0x157000, 0x200, 0x153200, 0x200)]


def rva2off(rva):
    for va, vs, rp, rs in SECS:
        if va <= rva < va + max(vs, rs):
            return rp + (rva - va)
    raise ValueError(f"rva 0x{rva:x} unmapped")


def main():
    d = bytearray(open(SRC, "rb").read())
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    magic = struct.unpack_from("<H", d, pe + 24)[0]
    dd = pe + 24 + (96 if magic == 0x10B else 112)
    imp_rva, imp_sz = struct.unpack_from("<II", d, dd + 8)
    o = rva2off(imp_rva)

    patched = []
    i = 0
    while True:
        ilt, ts, fc, name_rva, iat = struct.unpack_from("<IIIII", d, o + i * 20)
        if name_rva == 0:
            break
        dll = d[rva2off(name_rva):].split(b"\0")[0].decode()
        if dll.upper() == "KERNEL32.DLL":
            # the shim name is longer than KERNEL32.dll, so plant it in .rdata
            # zero padding and repoint the descriptor there
            name_slot = 0xC56A1  # file offset of a 32-byte zero run in .rdata
            name_rva_new = 0xC6AA1
            new_name = SHIM.encode() + b"\0"
            d[name_slot:name_slot + len(new_name)] = new_name
            struct.pack_into("<I", d, o + i * 20 + 12, name_rva_new)
            t = ilt or iat
            j = 0
            while True:
                ent = struct.unpack_from("<I", d, rva2off(t) + j * 4)[0]
                if ent == 0:
                    break
                if not (ent & 0x80000000):
                    nm = d[rva2off(ent) + 2:].split(b"\0")[0].decode()
                    if nm in TARGETS:
                        patched.append(nm)
                j += 1
        i += 1

    missing = TARGETS - set(patched)
    if missing:
        print(f"error: not patched: {sorted(missing)}", file=sys.stderr)
        return 1
    open(DST, "wb").write(d)
    print(f"patched {', '.join(sorted(patched))} -> {SHIM}")
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
