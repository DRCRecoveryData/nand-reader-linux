#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Start NAND_Reader.exe under Wine with its kernel32 device imports redirected.

The vendor tool opens \\.\NAND_Flash_Reader2 through a kernel driver Wine does
not have.  The shim (hook.c, exported as kernel32.dll) answers that handle and
relays the I/O to nanddev_proxy.py over AF_UNIX.  Wine does not let another
process patch a running image, so the child is started halted (SIGSTOP before
it executes), the six I/O entries of its KERNEL32 import table are pointed at
the shim's exports by writing /proc/<pid>/mem, and the halted loader resumes at
the child's unchanged entry point.

Usage: licr.py [--no-hook] [--detach] [--] [target.exe args...]
"""
import ctypes
import os
import re
import signal
import select
import socket
import threading
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.environ.get("LICR_SHIM", os.path.join(HERE, "kernel32.dll"))
NR_DIR = os.environ.get("LICR_DIR", "/tmp/cap2/drive_c/nr")
TARGET = os.path.join(NR_DIR, "NAND_Reader.exe")
HOOKS = ("CreateFileA", "CreateFileW", "WriteFile", "ReadFile",
         "CloseHandle", "DeviceIoControl")

libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
libc.personality.argtypes = [ctypes.c_ulong]
PTRACE_TRACEME, PTRACE_SEIZE, PTRACE_DETACH = 0, 0x4206, 17
PTRACE_INTERRUPT, PTRACE_POKEDATA = 0x4207, 5


class Mem:
    def __init__(self, pid):
        self.pid = pid
        self.fd = os.open(f"/proc/{pid}/mem", os.O_RDWR)

    def read(self, addr, n):
        return os.pread(self.fd, n, addr)

    def write(self, addr, data):
        n = os.pwrite(self.fd, data, addr)
        if n != len(data):
            raise OSError(f"short write at {addr:#x}")

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def u16(self, addr):
        return struct.unpack("<H", self.read(addr, 2))[0]

    def close(self):
        os.close(self.fd)


def mappings(pid):
    """(start, end, perms, path) for every mapping of pid."""
    out = []
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            m = re.match(r"([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)", line.rstrip())
            if m:
                out.append((int(m[1], 16), int(m[2], 16), m[3], m[4]))
    return out


def find_module(pid, name):
    """(base, path) of the mapped image called <name>[.exe|.dll]."""
    want = name[:-4] if name.lower().endswith(".exe") else name
    want = want.lower()
    for start, end, perms, path in mappings(pid):
        if path and path.rsplit("/", 1)[-1].lower() == want + ".dll":
            return start & ~0xFFF, path
    # Native PE images are mapped as deleted temp files or without a name,
    # so accept any named image whose PE header matches; otherwise take the MZ
    # image at the lowest module base.
    maps = mappings(pid)
    ends = {m[0] for m in maps}
    found = []
    with open(f"/proc/{pid}/mem", "rb", 0) as f:
        for start, end, perms, path in maps:
            if not perms.startswith("r") or start in ends:
                continue
            if path.startswith("/usr/") or path.startswith("/run/"):
                continue
            try:
                f.seek(start)
                if f.read(2) != b"MZ":
                    continue
                f.seek(start + 0x3C)
                nt = start + struct.unpack("<I", f.read(4))[0]
                f.seek(nt)
                if f.read(4) != b"PE\0\0":
                    continue
            except OSError:
                continue
            found.append((start, path))
    if not found:
        raise RuntimeError(f"{name} not mapped in {pid}")
    return min(found)


def resolve(pid, base, mem, name):
    pe = mem.u32(base + 0x3C)
    nt = base + pe
    if mem.read(nt, 4) != b"PE\0\0":
        raise RuntimeError("bad PE header")
    opt = nt + 24
    magic = mem.u16(opt)
    dd = opt + (96 if magic == 0x10B else 112)
    exp_rva, exp_size = struct.unpack("<II", mem.read(dd, 8))
    if not exp_rva:
        raise RuntimeError("no export table")
    e = base + exp_rva
    _flags, _ts, _maj, _min, _name, ord_base, nfun, nname, afun, anames, aords = \
        struct.unpack("<IIHHIIIIIII", mem.read(e, 40))
    lo, hi = 0, nname
    target = name.lower().encode()
    while lo < hi:
        mid = (lo + hi) // 2
        nm_off = mem.u32(base + anames + 4 * mid)
        nm = mem.read(base + nm_off, 128)
        nm = nm[:nm.index(b"\0")].lower()
        if nm < target:
            lo = mid + 1
        elif nm > target:
            hi = mid
        else:
            idx = mem.u16(base + aords + 2 * mid)
            return base + mem.u32(base + afun + 4 * idx)
    raise RuntimeError(f"{name} not exported")


def entry_point(path):
    d = open(path, "rb").read(0x1000)
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    opt = pe + 24
    return struct.unpack_from("<I", d, opt + 16)[0], struct.unpack_from("<I", d, opt + 28)[0]


MAIN = ["NAND_Reader.exe"]
SOCK = "/tmp/nanddev.sock"
PID = [0]
DEADLINE = 30
MAINTHREAD_GO = threading.Event()


def patch_final(pid):
    """Patch the IAT of the image that is already mapped, then let the shim out.

    The child is released without a tracer, so the thread-held stop of the
    PT_TRACEME wake-up cannot be reaped here; pthreads (not raw channels)
    manage the child's /proc/<pid>/mem and it is writable while it runs.
    """
    for _ in range(50):
        try:
            hits, src = patch(pid)
            break
        except RuntimeError:
            time.sleep(0.01)
    else:
        raise RuntimeError("import table not reachable")
    mem = Mem(pid)
    try:
        var = mem.u32(resolve(pid, find_module(pid, SHIM)[0], mem, "BusyFlag"))
        mem.write(var, struct.pack("<i", 1))
    finally:
        mem.close()
    return hits, src


def _patch_at(pid, mod):
    mem = Mem(pid)
    try:
        _, src = find_module(pid, MAIN[0])
        return _patch_iat(pid, mod, mem), src
    finally:
        mem.close()


def _patch_iat(pid, mod, mem):
    pe = mem.u32(mod + 0x3C)
    nt = mod + pe
    opt = nt + 24
    magic = mem.u16(opt)
    dd = opt + (96 if magic == 0x10B else 112)
    imp_rva = mem.u32(dd + 8)
    k32, _k32path = find_module(pid, "kernel32.dll")
    targets = {n: resolve(pid, k32, mem, n) for n in HOOKS}
    hits = []
    if os.environ.get("LICR_DEBUG"):
        print("mod %#x imp %#x" % (mod, imp_rva), file=sys.stderr)
    p = mod + imp_rva
    while True:
        ilt, _ts, _fc, name_rva, iat = struct.unpack("<IIIII", mem.read(p, 20))
        if not name_rva:
            break
        dll = mem.read(mod + name_rva, 32).split(b"\0")[0].decode("latin1")
        if os.environ.get("LICR_DEBUG"):
            print("  dll", dll, "ilt %#x iat %#x" % (ilt, iat), file=sys.stderr)
        if dll.upper() == "KERNEL32.DLL":
            t = mod + (ilt or iat)
            k = 0
            while True:
                ent = mem.u32(t + 4 * k)
                if not ent:
                    break
                if not ent & 0x80000000:
                    nm = mem.read(mod + ent + 2, 64).split(b"\0")[0].decode("latin1")
                    if nm in targets:
                        mem.write(mod + iat + 4 * k, struct.pack("<I", targets[nm]))
                        hits.append(nm)
                k += 1
        p += 20
    return hits


def patch(pid):
    mem = Mem(pid)
    try:
        mod, src = find_module(pid, MAIN[0])
        return _patch_iat(pid, mod, mem), src
    finally:
        mem.close()


def _state(pid):
    with open(f"/proc/{pid}/stat") as f:
        return f.read().split(") ")[-1][0]


class MemRetry:
    """A traced child drops live mappings from an already open mem fd at every
    stop, so a stale fd reads back empty; retry from a fresh one."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, pid):
        for _ in range(20):
            try:
                return self.fn(pid)
            except RuntimeError:
                time.sleep(0.01)
        raise RuntimeError("patch failed")


def wait_for(pid, pred, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            v = pred(mappings(pid))
            if v:
                return v
        except (OSError, RuntimeError):
            pass
        time.sleep(0.001)
    raise RuntimeError("timed out waiting for %s" % pid)


class Settle:
    """Patch window opened by the shim.

    The loader maps the image and then has user code call boot() while it is
    still finishing; the IAT is parsed at the very end of that phase, so it may
    only be rewritten after this first call returns.  The shim answers 'B'
    before it returns and then parks in its thread-local settle loop, which
    keeps the program from running while the proxy rewrites the table.
    """

    def __init__(self, do_patch):
        self.do_patch = do_patch
        if os.path.exists(SOCK):
            os.unlink(SOCK)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(SOCK)
        self.srv.listen(8)
        self.srv.settimeout(0.05)
        self.conn = None
        self.patched = False

    def __call__(self):
        deadline = time.time() + DEADLINE
        c = self.conn
        while time.time() < deadline:
            try:
                if c is None:
                    self.srv.settimeout(0.05)
                    c, _ = self.srv.accept()
                    self.conn = c
                c.settimeout(0.05)
                op = c.recv(1)
            except OSError:
                continue
            if not op:
                c = self.conn = None
                continue
            if op == b"B":
                c.sendall(b"\0")
                if not self.patched:
                    self.patched = True
                    self._patch()
                continue
            if op == b"O":
                return "device session started"
            if op == b"W":
                return "first device write"
            if op == b"R":
                return "first device read"
            if op == b"C":
                continue
            raise RuntimeError("unexpected op %r" % op)
        raise RuntimeError("shim did not reach the program in %ds" % DEADLINE)

    def _patch(self):
        try:
            hits, src = self.do_patch(PID[0])
            self.base = None
        except RuntimeError as e:
            print("patch failed:", e, flush=True)
            return
        print("hooked %d in %s" % (len(hits), src), flush=True)


    def go(self):
        pass                                             # the child execs on its own

    def close(self):
        for c in (self.conn, self.srv):
            if c:
                c.close()


def read_var(pid, dll, name):
    """Address of an exported data symbol (address stored in its export slot)."""
    mem = Mem(pid)
    try:
        base, _ = find_module(pid, dll)
        return mem.u32(resolve(pid, base, mem, name))
    except (RuntimeError, ValueError, struct.error):
        return None
    finally:
        mem.close()


def start(args, hook=True):
    """Launch wine with WINELOADERNOEXEC=1, then patch the IAT between the end
    of the loader's mapping phase and the first instruction of the program.

    The child is detached running as the very first thing, while Wine's VDSO
    still re-raises the exit stop as SIGTRAP (the process group is orphaned).
    The loader then maps the image (~150 ms) with user code calling the shim
    while it finishes, which is the handshake the patch window uses (Settle):
    on the device open the import table is rewritten and the shim's parked
    callers are released, in the same step.
    """
    env = dict(os.environ, WINELOADERNOEXEC="1")
    out = open("/tmp/nr.out", "w")                       # before fork: no text buffer
    ep = subprocess.Popen(["wine"] + args, cwd=NR_DIR, env=env,
                          stdout=out, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL,
                          preexec_fn=lambda: libc.ptrace(PTRACE_TRACEME, 0, None, None))
    os.waitpid(ep.pid, 0)                                # exec stop
    # yama only lets the tracer chosen at fork time own /proc/pid/mem, but a
    # traced loader never maps the image: detach at once, carrying SIGCONT.
    libc.ptrace(PTRACE_DETACH, ep.pid, None, ctypes.c_void_p(signal.SIGCONT))
    PID[0] = ep.pid
    settle = Settle(patch_final) if hook else None
    try:
        if settle:
            print(settle(), flush=True)
            settle.go()
    finally:
        if settle:
            settle.close()
    return ep


def main():
    argv = sys.argv[1:]
    hook = True
    detach = False
    while argv and argv[0].startswith("--"):
        a = argv.pop(0)
        if a == "--no-hook":
            hook = False
        elif a == "--detach":
            detach = True
        elif a == "--":
            break
        else:
            print(__doc__)
            return 2
    args = argv or [TARGET]
    MAIN[0] = args[0].replace("\\", "/").rsplit("/", 1)[-1]
    p = start(args, hook)
    if detach:
        print("pid", p.pid)
        return 0
    return p.wait()


if __name__ == "__main__":
    sys.exit(main())
