#!/usr/bin/env python3
"""Point only the four device imports at nanddev.dll, leaving KERNEL32 intact.

The whole-descriptor swap in patch_imports.py replaces kernel32 and starves the
process of its loader-time exports. This keeps KERNEL32.dll as the descriptor
and appends a second descriptor that names nanddev.dll, then repoints just the
CreateFileA / WriteFile / ReadFile / CloseHandle hint/name entries to it.
"""
import struct
import sys

TARGETS = {"CreateFileA", "WriteFile", "ReadFile", "CloseHandle"}
SHIM = b"nanddev.dll\0"


def main(src, dst):
    d = bytearray(open(src, "rb").read())
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    imp_rva = struct.unpack_from("<I", d, dd + 8)[0]
    sec = opt + struct.unpack_from("<H", d, pe + 20)[0]
    vsz = struct.unpack_from("<I", d, opt + 56)[0]   # SizeOfImage

    new_sec = {}  # rva -> file offset, filled once the section exists

    def roff(rva):
        for i in range(nsec):
            s = sec + i * 40
            va, vs, raw = (struct.unpack_from("<I", d, s + 12)[0],
                           struct.unpack_from("<I", d, s + 8)[0],
                           struct.unpack_from("<I", d, s + 20)[0])
            if va <= rva < va + vs:
                return raw + (rva - va)
        r = new_sec.get(rva)
        if r is None:
            raise ValueError(f"rva 0x{rva:x} is not mapped")
        return r

    o = roff(imp_rva)
    k = None
    n = 0
    while True:
        desc = o + n * 20
        ilt, ts, fc, name_rva, iat = struct.unpack_from("<IIIII", d, desc)
        if name_rva == 0:
            end = desc
            break
        if b"KERNEL32" in bytes(d[roff(name_rva):roff(name_rva) + 12]).upper():
            k = (desc, ilt, iat)
        n += 1
    if k is None:
        raise SystemExit("no KERNEL32 descriptor")
    kdesc, kilt, kiat = k
    print(f"kernel32 descriptor @0x{kdesc:x} ilt=0x{kilt:x} iat=0x{kiat:x}")

    # every ILT slot in KERNEL32, matched by name
    slots = []
    j = 0
    while True:
        ent = struct.unpack_from("<I", d, roff(kilt) + j * 4)[0]
        if ent == 0:
            break
        if not (ent & 0x80000000):
            nm = bytes(d[roff(ent) + 2:].split(b"\0")[0])
            if nm.decode(errors="replace") in TARGETS:
                slots.append((j, nm))
        j += 1
    print("found", [s[1].decode() for s in slots])
    if {s[1].decode() for s in slots} != TARGETS:
        raise SystemExit("not all targets present")

    # New read/write section .nand appended to the file: 20 B import
    # descriptor, 20 B ILT, 20 B IAT, four hint/name pairs, dll name.
    salign = struct.unpack_from("<I", d, opt + 32)[0]
    falign = struct.unpack_from("<I", d, opt + 36)[0]
    last = sec + (nsec - 1) * 40
    last_va = struct.unpack_from("<I", d, last + 12)[0]
    last_vs = struct.unpack_from("<I", d, last + 8)[0]
    rva_off = struct.unpack_from("<I", d, last + 20)[0] - last_va
    last_end_rva = last_va + ((last_vs + salign - 1) // salign) * salign
    if rva_off > 0:
        last_end_rva += (rva_off + salign - 1) // salign * salign   # same section alignment offset as the last section
    raw_end = len(d)
    raw_off = (raw_end + falign - 1) // falign * falign
    size = 512
    if sec + (nsec + 1) * 40 > struct.unpack_from("<I", d, opt + 60)[0]:
        raise SystemExit("no room for a new section header")
    hdr = sec + nsec * 40
    name = b".nand\0\0\0"
    d[hdr:hdr + 40] = name + struct.pack("<IIIIIIHHI",
                                          size, last_end_rva, size, raw_off,
                                          0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<H", d, pe + 6, nsec + 1)
    new_img = (last_end_rva + size + salign - 1) // salign * salign
    struct.pack_into("<I", d, opt + 56, new_img)
    if raw_off > len(d):
        d += b"\0" * (raw_off - len(d))
    d += b"\0" * (raw_off + size - len(d))
    assert len(d) == raw_off + size, (len(d), raw_off, size)

    base = raw_off
    tail_rva = last_end_rva

    def va(off):
        return tail_rva + (off - base)

    desc_off = base
    ilt_off = base + 20
    iat_off = base + 40
    name_off = base + 60
    name_rvas = {}
    for _, nm in slots:
        name_rvas[nm] = va(name_off)
        d[name_off:name_off + 2] = b"\0\0"
        d[name_off + 2:name_off + 2 + len(nm) + 1] = nm + b"\0"
        name_off += 2 + len(nm) + 1
        name_off = (name_off + 1) & ~1
    shim_rva = va(name_off)
    d[name_off:name_off + len(SHIM)] = SHIM

    order = [nm for _, nm in slots]
    for idx in range(5):
        v = name_rvas[order[idx]] if idx < len(order) else 0
        struct.pack_into("<I", d, ilt_off + idx * 4, v)
        struct.pack_into("<I", d, iat_off + idx * 4, v)
    assert name_off + len(SHIM) <= base + size

    struct.pack_into("<IIIII", d, desc_off,
                     va(ilt_off), 0, len(order), shim_rva, va(iat_off))
    print(f"new descriptor @0x{va(desc_off):x} ilt=0x{va(ilt_off):x} "
          f"iat=0x{va(iat_off):x} name=0x{shim_rva:x}")

    # repoint the four KERNEL32 slots to the new descriptor's name strings;
    # the loader resolves each thunk from the descriptor that owns its slot,
    # so the hint/name entries must be reachable from a descriptor for
    # nanddev.dll. Chain that descriptor in after the last real one.
    struct.pack_into("<IIIII", d, end, va(ilt_off), 0, len(order),
                     shim_rva, va(iat_off))
    struct.pack_into("<IIIII", d, end + 20, 0, 0, 0, 0, 0)
    for idx, nm in slots:
        struct.pack_into("<I", d, roff(kilt) + idx * 4, name_rvas[nm])
        struct.pack_into("<I", d, roff(kiat) + idx * 4, name_rvas[nm])
        print(f"  thunk {nm.decode()} -> nanddev")

    # recompute the PE checksum
    chk = pe + 24 + 64
    struct.pack_into("<I", d, chk, 0)
    total = 0

    def s16(buf, acc):
        if len(buf) & 1:
            buf += b"\0"
        for i in range(0, len(buf), 2):
            acc += buf[i] | (buf[i + 1] << 8)
            acc = (acc & 0xFFFF) + (acc >> 16)
        return acc

    total = s16(bytes(d[:sec]), total)
    for i in range(nsec):
        s = sec + i * 40
        rs = struct.unpack_from("<I", d, s + 16)[0]
        rp = struct.unpack_from("<I", d, s + 20)[0]
        if rs:
            total = s16(bytes(d[rp:rp + rs]), total)
    total = s16(b"\0\0", total)
    struct.pack_into("<I", d, chk, ((total & 0xFFFF) + len(d)) & 0xFFFFFFFF)

    open(dst, "wb").write(d)
    print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
