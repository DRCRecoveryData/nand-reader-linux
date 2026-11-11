#!/usr/bin/env python3
"""Bridge between the vendor NAND_Reader (under Wine) and the physical reader.

The shim kernel32.dll forwards every device call of NAND_Reader.exe to this
proxy over an AF_UNIX socket; the proxy performs it on the real device through
libusb and logs the whole transaction to NAND_DEV_LOG so the vendor's exact
USB sequence for a given operation can be reproduced natively on Linux.

Wire (little endian lengths):
    shim -> proxy   'W' u32 len payload                     WriteFile
                    'R' u32 len                             ReadFile
                    'I' u32 code u32 inlen u32 outlen p i   DeviceIoControl
                    'O'                                     open  (port reset)
                    'C'                                     close
                    'B'                                     loader mapping done
    proxy -> shim   u8 ok | u32 done | u8 err                write/ioctl/open
                    u32 len + bytes | u8 err                read

A write of a full configuration image marks a new boot: the proxy power-cycles
the FGPA through the YKUSH unless NAND_DEV_NOCYCLE=1.
"""
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usb.core
import usb.util

EP_OUT = 0x02
EP_IN = 0x86
IFACE = 0
VID, PID = 0x03EB, 0x5903
WRITE_TIMEOUT_MS = int(os.environ.get("NAND_DEV_WRITE_TIMEOUT_MS", "30000"))
READ_TIMEOUT_MS = int(os.environ.get("NAND_DEV_READ_TIMEOUT_MS", "8000"))
SOCK = os.environ.get("NAND_DEV_SOCK", "/tmp/nanddev.sock")
LOG = os.environ.get("NAND_DEV_LOG", "/tmp/nanddev.log")
CYCLE = os.environ.get("NAND_DEV_NOCYCLE") != "1"
IMAGE_MIN = 1024

log = open(LOG, "w", buffering=1)


def L(*a):
    print(*a, file=log)


dev = None


def open_dev():
    global dev
    if CYCLE and os.environ.get("NAND_DEV_NOCYCLE") is None:
        import nand_linux
        nand_linux.bus_reset()
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("reader not found")
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    try:
        if dev.is_kernel_driver_active(IFACE):
            dev.detach_kernel_driver(IFACE)
    except usb.core.USBError:
        pass
    usb.util.claim_interface(dev, IFACE)
    L("OPEN bus=%s addr=%s" % (dev.bus, dev.address))


def close_dev():
    global dev
    if dev is not None:
        try:
            usb.util.release_interface(dev, IFACE)
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(dev)
        dev = None


def recvn(conn, n):
    b = b""
    while len(b) < n:
        p = conn.recv(n - len(b))
        if not p:
            raise EOFError
        b += p
    return b


def run(conn):
    while True:
        try:
            op = recvn(conn, 1)
            if op == b"O":
                if dev is None:
                    open_dev()
                    conn.sendall(b"\x00\x00\x00\x00\x00\x00")
                else:
                    L("reopen (device kept)")
                    conn.sendall(b"\x00\x00\x00\x00\x00\x00")
                continue
            if op == b"C":
                L("CLOSE (device kept for next session)")
                continue
            if op == b"B":
                # the shim has finished the loader's mapping phase and is about
                # to enter the program image; nothing will re-read the IAT now
                L("BUSY")
                conn.sendall(b"\x00")
                continue
            if op == b"W":
                n = struct.unpack("<I", recvn(conn, 4))[0]
                buf = recvn(conn, n)
                t0 = time.time()
                # a full configuration image starts a new FPGA boot: the bitstream
                # write resets the FPGA itself, so no bus reset is needed here
                rep = b""
                try:
                    if dev is None:
                        open_dev()
                    if n >= IMAGE_MIN:
                        L("W %d [image] %s ..." % (n, buf[:16].hex()))
                        sent = 0
                        while sent < n:
                            sent += dev.write(EP_OUT, buf[sent:sent + (64 << 10)],
                                              WRITE_TIMEOUT_MS)
                        L("  streamed %d in %.0f ms" % (n, (time.time() - t0) * 1000))
                        if buf[:3] in (b"\xff\xff\xff", b"") or True:
                            # FPGA re-enumerated with the new design: boot replies
                            for k in range(3):
                                try:
                                    r = bytes(dev.read(EP_IN, 4096, 3000))
                                    L("  boot reply %d: %d %s" % (k, len(r), r[:32].hex()))
                                    rep = r
                                    break
                                except usb.core.USBError as exc:
                                    L("  no boot reply %d: %s" % (k, exc.errno))
                    else:
                        done = dev.write(EP_OUT, buf, WRITE_TIMEOUT_MS)
                        L("W %d -> %d %.0fms %s" % (n, done, (time.time() - t0) * 1000,
                                                    buf[:64].hex()))
                    conn.sendall(b"\x00" + struct.pack("<I", n) + b"\x00")
                except Exception as exc:
                    L("W %d -> ERR %s" % (n, repr(exc)))
                    conn.sendall(b"\x00" + struct.pack("<I", 0) + b"\x03")
            elif op == b"R":
                n = struct.unpack("<I", recvn(conn, 4))[0]
                t0 = time.time()
                try:
                    rep = bytes(dev.read(EP_IN, n, READ_TIMEOUT_MS))
                    L("R %d -> %d %.0fms %s" % (n, len(rep), (time.time() - t0) * 1000,
                                                rep[:64].hex()))
                    conn.sendall(struct.pack("<I", len(rep)) + rep + b"\x00")
                except usb.core.USBError as exc:
                    L("R %d -> ERR %s %.0fms" % (n, exc.errno, (time.time() - t0) * 1000))
                    conn.sendall(struct.pack("<I", 0) + b"\x01")
            elif op == b"I":
                code, inlen, outlen = struct.unpack("<III", recvn(conn, 12))
                data = recvn(conn, inlen)
                L("I 0x%08x in=%d out=%d %s" % (code, inlen, outlen, data[:32].hex()))
                # WinUSB calls the vendor might issue: pass through as bulk I/O
                done = 0
                try:
                    rep = b""
                    if inlen:
                        done = dev.write(EP_OUT, data, WRITE_TIMEOUT_MS)
                    if outlen:
                        rep = bytes(dev.read(EP_IN, outlen, READ_TIMEOUT_MS))
                    conn.sendall(b"\x00" + struct.pack("<I", len(rep)) + b"\x00")
                    L("  -> in %d out %d" % (done, len(rep)))
                except usb.core.USBError as exc:
                    L("  -> ERR %s" % exc.errno)
                    conn.sendall(struct.pack("<I", 0) + b"\x01")
            else:
                L("BAD op %r" % op)
                break
        except EOFError:
            break
        except Exception as exc:  # keep the log useful whatever the vendor does
            L("EXC", repr(exc))
            try:
                conn.sendall(struct.pack("<I", 0) + b"\x01")
            except OSError:
                break


def main():
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(1)
    L("LISTEN", SOCK)
    while True:
        conn, _ = srv.accept()
        L("CONNECT")
        try:
            run(conn)
        except (EOFError, ConnectionError, OSError) as e:
            L("SESSION END", type(e).__name__)
        if os.environ.get("NAND_DEV_ONCE") == "1":
            close_dev()
            return


if __name__ == "__main__":
    main()
