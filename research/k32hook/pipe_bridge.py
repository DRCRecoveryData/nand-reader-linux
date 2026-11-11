#!/usr/bin/env python3
r"""Bridge pipesrv.exe (Wine, TCP 127.0.0.1:47199) <-> AF_UNIX nanddev proxy.

Wine 9.0 ws2_32 refuses AF_UNIX (10047), so the injected shim talks to
\\.\pipe\nanddev; pipesrv.exe relays that to TCP; this script relays TCP to
/tmp/nanddev.sock.  One TCP connection maps to one proxy session.
"""
import os
import socket
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("NMD_PORT", "47199"))
SOCK = os.environ.get("NAND_DEV_SOCK", "/tmp/nanddev.sock")
PREFIX = os.environ.get("WINEPREFIX", "/tmp/cap4")


def relay(src, dst, tag):
    try:
        while True:
            d = src.recv(65536)
            if not d:
                break
            dst.sendall(d)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(conn):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    print("bridge: proxy connected", flush=True)
    t = threading.Thread(target=relay, args=(s, conn, "p"), daemon=True)
    t.start()
    relay(conn, s, "t")
    t.join(timeout=3)
    s.close()
    conn.close()
    print("bridge: session done", flush=True)


def spawn_helper():
    env = dict(os.environ, WINEPREFIX=PREFIX, WINEDEBUG="-all")
    env.pop("WINELOADERNOEXEC", None)
    return subprocess.Popen(
        ["wine", os.path.join(HERE, "pipesrv.exe"), str(PORT)],
        env=env, stdout=None, stderr=None)


def main():
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", PORT))
    ls.listen(4)
    print(f"bridge: listening 127.0.0.1:{PORT} -> {SOCK}", flush=True)
    helper = spawn_helper()
    try:
        while True:
            c, _ = ls.accept()
            print("bridge: tcp from pipesrv", flush=True)
            threading.Thread(target=handle, args=(c,), daemon=True).start()
            if helper.poll() is not None:
                print(f"bridge: pipesrv exited {helper.returncode}, respawning", flush=True)
                helper = spawn_helper()
    finally:
        helper.terminate()


if __name__ == "__main__":
    sys.exit(main())
