#!/usr/bin/env python3
"""Patch the six DEVICE imports of a running NAND_Reader.exe to k32hook.dll.

Wine ignores the shim kernel32.dll next to the exe, and the tracer dance in
licr.py is blocked by yama after a cold boot; as root /proc/<pid>/mem is
writable directly.  k32hook.dll must already be mapped in the process (its
DllMain also rewrites the table, this makes the write explicit and releases
BusyFlag if that DllMain has not run).

Usage: sudo python3 rootpatch.py [pid]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import licr

IMAGE = "NAND_Reader.exe"


def find_image(pid):
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            if line.rstrip().endswith("/" + IMAGE):
                return int(line.split("-", 1)[0], 16)
    raise RuntimeError(f"{IMAGE} not mapped in {pid}")


def find_dll(pid, name):
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            if line.rstrip().lower().endswith("/" + name.lower()):
                return int(line.split("-", 1)[0], 16) & ~0xFFF
    raise RuntimeError(f"{name} not mapped in {pid}")


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.popen("pgrep -x " + IMAGE).read().split()[0])
    base = find_image(pid)
    mem = licr.Mem(pid)
    try:
        src = "/" + IMAGE
        print("image at %#x" % base)
        dll = find_dll(pid, "k32hook.dll")
        var = mem.u32(licr.resolve(pid, dll, mem, "BusyFlag"))
        mem.write(var, struct.pack("<i", 1))
        print("BusyFlag at %#x released" % var)
    finally:
        mem.close()


if __name__ == "__main__":
    main()
