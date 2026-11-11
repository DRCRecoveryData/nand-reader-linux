#!/usr/bin/env python3
"""Linux port of FlashExtractor factory/recovery/restore.py.

Streams the vendor FPGA configuration (power adapter design, NAND reader
design, or the reader's init->program->control sequence) into the Cyclone
EP1C3 through libusb instead of kernel32/FlashReader2.sys.

The EP1C3 is configured from SRAM in clear, so a board that has just been
power-cycled (the USB cable is the only thing that drops board power) comes
up unconfigured and can be re-streamed. Restoring while the board is already
configured is what wedges the SX2, hence the --replugged gate.

Every image is sha256-gated before anything goes on the wire. Nothing here
touches the NAND chip itself: no program, no erase.

  python3 restore_linux.py                    # power design + control, verify
  python3 restore_linux.py --image map        # reader init->program->control
  python3 restore_linux.py --check --dry-run  # hashes only, no USB
"""

import argparse
import hashlib
import os
import sys
import threading
import time

import nand_linux as nl
import ykush

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IMAGES = os.environ.get("FLASHEXTRACTOR_RECOVERY", "/tmp/fxr/factory/recovery")

IMAGES = {
    "power": ("vendor_power_init_segment.bin",
              "6c6140635b6b8cb8b13dbd0138b3d2a97964f794d8e4f8813303ece4ae7b7264"),
    "map": ("vendor_init_segment.bin",
            "d2c3da709d64be6f5996485536d8394ca8680464d4e547127243058d203896e0"),
}
READER = [
    ("init", "vendor_init_segment.bin",
     "d2c3da709d64be6f5996485536d8394ca8680464d4e547127243058d203896e0"),
    ("program", "vendor_program.bin",
     "2b1bc2032f50ed18dba92a6eb009e2ff1493e2bc979f6d52188dd47e517a80a8"),
    ("control", "vendor_control.bin",
     "6925c7e5f33e92331d4c15e58fac2fc980289a255ed0603fb08eb032df0e17e2"),
]
CONTROL = bytes.fromhex("4d80")
EXPECT = bytes.fromhex("4d800fe0")  # 327 cV echo from the power design
PROGRAM_SHA = READER[1][2]


class RestoreError(RuntimeError):
    pass


class Watchdog:
    """Exit hard if a USB transfer wedges (mirrors restore.py's watchdog)."""

    def __init__(self, timeout):
        self.timeout = timeout
        self._event = threading.Event()
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        if not self._event.wait(self.timeout):
            print(f"watchdog: no completion after {self.timeout:.0f}s, exiting",
                  file=sys.stderr, flush=True)
            os._exit(4)

    def cancel(self):
        self._event.set()


def load_image(path, sha):
    with open(path, "rb") as fh:
        data = fh.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha:
        raise RestoreError(f"{os.path.basename(path)}: sha256 {digest[:12]} "
                           f"!= expected {sha[:12]}")
    return data


def resolve(pdir, name):
    path = os.path.join(pdir, name)
    if not os.path.isfile(path):
        raise RestoreError(f"missing image {path}")
    return path


def check_images(pdir):
    for name, sha in [*IMAGES.values(), *[(n, s) for _, n, s in READER]]:
        path = resolve(pdir, name)
        data = load_image(path, sha)
        print(f"  ok {len(data):>9d}  {sha[:12]}  {name}")
    # nand_linux assembles its 2048 B program block in code; the vendor image
    # must match it byte for byte or the two drivers would emit different reads.
    if load_image(resolve(pdir, "vendor_program.bin"), PROGRAM_SHA) != nl.id_table():
        raise RestoreError("vendor_program.bin differs from nand_linux.id_table()")


def write_blob(r, data, label):
    if len(data) >= nl.PWR_IMAGE_LEN:
        r._chunked(data)
    else:
        r._out(data)
    print(f"  wrote {len(data):>9d}  {label}", file=sys.stderr)


def do_power(pdir, settle, timeout, control, check):
    path = resolve(pdir, IMAGES["power"][0])
    data = load_image(path, IMAGES["power"][1])
    with nl.Reader() as r:
        write_blob(r, data, "power design")
        time.sleep(settle)
        if control:
            r._out(CONTROL)
            print("  wrote       2  control 4d80", file=sys.stderr)
        if check:
            time.sleep(settle)
            reply = bytes(r.dev.read(nl.EP_IN, len(EXPECT), int(timeout * 1000)))
            print(f"  read  {reply.hex(' ')}")
            if reply != EXPECT:
                raise RestoreError(f"expected {EXPECT.hex()}, got {reply.hex()}")
            print(f"RESULT PASS {nl.decode_cv(reply)} centivolts")


def do_map(pdir, settle, timeout, control, read_len):
    blobs = [(load_image(resolve(pdir, name), sha), label)
             for label, name, sha in READER if control or label != "control"]
    with nl.Reader() as r:
        for i, (data, label) in enumerate(blobs):
            write_blob(r, data, label)
            if i + 1 < len(blobs):
                time.sleep(settle)
        if read_len:
            time.sleep(settle)
            reply = bytes(r.dev.read(nl.EP_IN, read_len, int(timeout * 1000)))
            print(f"  read  {reply.hex(' ')}")
    print("RESULT OK reader design configured")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", choices=("power", "map"), default="power")
    ap.add_argument("--images", default=DEFAULT_IMAGES,
                    help="directory holding the vendor images")
    ap.add_argument("--settle", type=float, default=0.25)
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="watchdog seconds per run (read timeout is 20 s)")
    ap.add_argument("--no-control", action="store_true",
                    help="power image only (no 4d80 DAC/control word)")
    ap.add_argument("--check", action="store_true",
                    help="power image: verify the 4d800fe0 echo")
    ap.add_argument("--read", type=int, default=0,
                    help="reader design: read N bytes after the control block")
    ap.add_argument("--dry-run", action="store_true", help="verify images only")
    gate = ap.add_mutually_exclusive_group()
    gate.add_argument("--replugged", action="store_true",
                      help="confirm the reader was unplugged/replugged so the "
                           "EP1C3 is unconfigured")
    gate.add_argument("--power-cycle", action="store_true",
                      help="drop and restore reader power through the YKUSH XS "
                           "instead of replugging by hand")
    args = ap.parse_args()

    pdir = os.path.abspath(args.images)
    print(f"images: {pdir}")
    check_images(pdir)
    if args.image == "power":
        print(f"power design {nl.POWER_INIT_SHA}  control={'off' if args.no_control else 'on'}  "
              f"check={'on' if args.check else 'off'}")
    else:
        print(f"reader design {nl.NAND_INIT_SHA}  control={'off' if args.no_control else 'on'}  "
              f"read={args.read}")

    if args.dry_run:
        print("RESULT DRY-RUN images verified, nothing sent")
        return
    if args.power_cycle:
        try:
            ykush.power_cycle(log=lambda m: print(m, file=sys.stderr))
        except ykush.YkushError as exc:
            sys.exit(f"FAIL {exc}")
    elif not args.replugged:
        print("REFUSING to stream: only a power cycle asserts nCONFIG. Unplug "
              "and replug the reader (the cable is the only thing that drops "
              "board power), or use --power-cycle with the YKUSH XS.")
        sys.exit(1)

    try:
        watchdog = Watchdog(args.timeout)
        try:
            if args.image == "power":
                do_power(pdir, args.settle, args.timeout, not args.no_control,
                         args.check)
            else:
                do_map(pdir, args.settle, args.timeout, not args.no_control,
                       args.read)
        finally:
            watchdog.cancel()
    except (RestoreError, OSError, nl.usb.core.USBError) as exc:
        sys.exit(f"FAIL {exc}")


if __name__ == "__main__":
    main()
